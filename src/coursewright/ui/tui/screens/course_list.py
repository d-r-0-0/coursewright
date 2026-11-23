"""Course List Screen for the Coursewright TUI.

Displays a scrollable DataTable of courses with columns: #, Code, Name,
Term, Students, Status. Includes a sidebar showing active filters and
keyboard shortcuts. Users can select courses by clicking or pressing
number keys, which pushes forward to the workflow screens.

Remote activity is always visible (docs/HOUSE_STYLE.md section 3): the
slow Canvas fetches run under the transmission log, and the quiet
background refreshes (participation dates, average grades) show a
loading indicator in the sidebar while they run.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import DataTable, Header, LoadingIndicator, Static
from textual.worker import Worker, WorkerState

from coursewright.core.config.semester import (
    detect_current_semester,
    semester_matches_term,
)
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.screens.semester_picker import SemesterPickerScreen
from coursewright.ui.tui.screens.status_picker import StatusPickerScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.input_modal import InputModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

# Workflow screens the Tools shortcuts open.  The list screen imports
# each one only when its key is pressed (they are large and pull in
# Canvas-backed workers), and if a screen module is ever missing from a
# build it says so plainly instead of the key silently doing nothing.
TOOL_SCREEN_LOCATIONS: dict[str, tuple[str, str]] = {
    "discussions": ("coursewright.ui.tui.screens.discussion_list", "DiscussionListScreen"),
    "gradeliner": ("coursewright.ui.tui.screens.gradeliner", "GradeLinerScreen"),
    "commenter": ("coursewright.ui.tui.screens.bulk_comment", "BulkCommentScreen"),
    "zybooks": ("coursewright.ui.tui.screens.zybooks", "ZyBooksScreen"),
    "packet tracer": ("coursewright.ui.tui.screens.packet_tracer", "PacketTracerScreen"),
    "zero ungraded": ("coursewright.ui.tui.screens.zero_ungraded", "ZeroUngradedScreen"),
    "attendance": ("coursewright.ui.tui.screens.attendance", "AttendanceScreen"),
    "course design": ("coursewright.ui.tui.screens.design_flow", "CourseDesignScreen"),
}

BACKGROUND_PARTICIPATION_KEY = "participation"
BACKGROUND_AVERAGE_GRADE_KEY = "average grade"


def find_tool_screen_class(tool_name: str) -> type | None:
    """Return the screen class for ``tool_name``, or ``None`` if not yet ported.

    Only a missing *screen module* counts as "not yet ported".  An
    import error from inside an existing screen is a real bug and is
    allowed to surface.
    """
    module_name, class_name = TOOL_SCREEN_LOCATIONS[tool_name]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as missing:
        if missing.name == module_name:
            return None
        raise missing from None
    return getattr(module, class_name)


@dataclass
class GradingTodoEntry:
    """One past-due assignment row for the course-list grading to-do panel."""

    assignment_name: str
    due_at: datetime
    ungraded_count: int


class ShortcutLink(Static):
    """Compact clickable shortcut label for the help sidebar.

    Replaces Textual Button widgets whose internal component styling
    overrides color rules and renders labels invisible at height=1.
    This thin Static subclass posts a Clicked message on click so
    the parent screen can dispatch the action.
    """

    DEFAULT_CSS = """
    ShortcutLink {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    ShortcutLink:hover {
        background: $primary 25%;
        color: $text;
        text-style: bold;
    }
    """

    class Clicked(Message):
        """Posted when the shortcut link is clicked."""

        def __init__(self, link: ShortcutLink) -> None:
            super().__init__()
            self.link = link

    def on_click(self) -> None:
        """Forward click to parent as a typed message."""
        self.post_message(self.Clicked(self))


class CourseListScreen(CoursewrightScreen):
    """Main course listing screen with table, filters, and help sidebar.

    Lifecycle:
    1. Receives AppState (populated by InitScreen) with filtered_courses.
    2. Builds a DataTable with course rows on compose/mount.
    3. User selects a course (click row or press number key 1-9).
    4. On selection, sets app_state.current_course and pushes the
       workflow screens.
    5. Optional filters/actions update filtered_courses and refresh table.

    Key bindings:
    - 1-9      : Select course by index (first 9 visible rows; the
                 sidebar hint shows only the keys that match a row)
    - d        : Push discussion list for selected course
    - g        : Push GradeLiner screen for selected course
    - b        : Push bulk commenter screen for selected course
    - w        : Push the five-phase course-design flow
    - r        : Refresh courses from Canvas
    - Escape/q : Back/Quit (inherited from CoursewrightScreen)
    """

    DEFAULT_CSS = """
    #left-panel {
        width: 1fr;
        height: 100%;
    }

    #table-container {
        height: 1fr;
    }

    #grading-todo-panel-scroll {
        border: solid $primary 30%;
        padding: 0 1;
        height: auto;
        max-height: 50%;
    }

    #grading-todo-panel {
        height: auto;
    }

    #background-activity {
        height: auto;
    }

    #background-loading {
        height: 1;
    }

    #background-status {
        height: auto;
        color: $text-muted;
    }

    #help-panel {
        border: solid $primary 30%;
        padding: 0 1;
        height: auto;
    }

    #help-panel-title {
        margin: 0 0 1 0;
        color: $success;
        text-style: bold;
    }

    .shortcut-group {
        margin: 0;
        height: auto;
    }

    .shortcut-group-title {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 0;
    }

    ShortcutLink {
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("t", "select_semester", "Semester", show=True, priority=False),
        Binding("f", "filter_term", "Filter Term", show=True, priority=False),
        Binding("n", "filter_course_number", "Course #", show=True, priority=False),
        Binding("s", "filter_status", "Status", show=True, priority=False),
        Binding("c", "clear_filters", "Clear Filters", show=True, priority=False),
        Binding("d", "open_discussions", "Discussions", show=True, priority=False),
        Binding("g", "open_gradeliner", "NetAcad Import", show=True, priority=False),
        Binding("y", "open_zybooks", "zyBooks Import", show=True, priority=False),
        Binding("b", "open_commenter", "Commenter", show=True, priority=False),
        Binding("p", "open_packet_tracer", "PT Checker", show=True, priority=False),
        Binding("a", "open_attendance", "Attendance", show=True, priority=False),
        Binding("m", "change_model", "Model", show=True, priority=False),
        Binding("z", "open_zero_ungraded", "Zero Ungraded", show=True, priority=False),
        Binding("w", "open_course_design", "Design", show=True, priority=False),
        Binding("r", "refresh_courses", "Refresh", show=True, priority=False),
        # Number keys for quick selection (1-9)
        Binding("1", "select_course(0)", "Select 1", show=False),
        Binding("2", "select_course(1)", "Select 2", show=False),
        Binding("3", "select_course(2)", "Select 3", show=False),
        Binding("4", "select_course(3)", "Select 4", show=False),
        Binding("5", "select_course(4)", "Select 5", show=False),
        Binding("6", "select_course(5)", "Select 6", show=False),
        Binding("7", "select_course(6)", "Select 7", show=False),
        Binding("8", "select_course(7)", "Select 8", show=False),
        Binding("9", "select_course(8)", "Select 9", show=False),
    ]

    # Number keys only cover the first nine rows (bindings "1" through "9").
    MAXIMUM_NUMBER_KEY_SELECTABLE_COURSES = 9

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        # Track which courses are visible in the table (for number key selection)
        self._visible_courses: list = []
        # Internal cache of all courses before filtering
        self._all_courses: list = []
        # Per-course cache to avoid refetching assignment metadata while navigating.
        self._grading_todo_cache: dict[int, tuple[datetime, list[GradingTodoEntry]]] = {}
        self._grading_todo_cache_ttl = timedelta(minutes=5)
        self._grading_todo_request_id = 0
        self._latest_grading_todo_request_id = 0
        self._latest_grading_todo_course_id: int | None = None
        self._grading_todo_transmission_modal: TransmissionModal | None = None
        # Quiet background Canvas fetches currently running, by key.
        self._background_activity: dict[str, str] = {}
        self._background_request_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        """Build the layout: header, main (table + sidebar), footer."""
        yield Header(show_clock=True)
        with Horizontal(id="course-list-main"):
            # Left: course table + grading to-do panel
            with Vertical(id="left-panel"):
                with VerticalScroll(id="table-container"):
                    yield DataTable(id="course-table", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="grading-todo-panel-scroll"):
                    yield Static(self._build_grading_todo_placeholder(), id="grading-todo-panel")
            # Right: sidebar with filters + help
            with Vertical(id="sidebar"):
                yield Static(
                    self._build_filter_panel_text(),
                    id="filter-panel",
                )
                # Shown only while a background Canvas fetch is running.
                with Vertical(id="background-activity"):
                    yield LoadingIndicator(id="background-loading")
                    yield Static("", id="background-status")
                with Vertical(id="help-panel"):
                    yield Static("Clickable Shortcuts", id="help-panel-title")
                    yield from self._compose_shortcut_buttons()
        yield HelpBar()

    def on_mount(self) -> None:
        """Populate the course table on mount."""
        self._render_background_activity()
        # Hide the number-key hint until a table population tells us how
        # many rows exist (the background fetch path populates later).
        self._update_course_selection_hint()
        has_cached_courses = bool(self.app_state.courses or self.app_state.filtered_courses)
        if not has_cached_courses and self.app_state.canvas_client is not None:
            self._fetch_initial_course_list_worker()
            return
        # Cache all courses for filtering
        self._all_courses = self.app_state.courses or self.app_state.filtered_courses
        table = self.query_one("#course-table", DataTable)
        self._populate_table(table)
        self._refresh_grading_todo_from_cursor()
        self._refresh_last_participation_async()
        self._refresh_average_grade_async()

    # ------------------------------------------------------------------
    # Background activity indicator
    # ------------------------------------------------------------------
    def _begin_background_activity(self, key: str, description: str) -> None:
        """Show the sidebar loading indicator for a quiet Canvas fetch."""
        self._background_activity[key] = description
        self._background_request_counts[key] = self._background_request_counts.get(key, 0) + 1
        self._render_background_activity()

    def _end_background_activity(self, key: str) -> None:
        """Forget a finished fetch; the indicator hides when none remain."""
        remaining = self._background_request_counts.get(key, 1) - 1
        if remaining:
            self._background_request_counts[key] = remaining
        else:
            self._background_request_counts.pop(key, None)
            self._background_activity.pop(key, None)
        self._render_background_activity()

    def _render_background_activity(self) -> None:
        """Repaint the sidebar indicator from the running fetches."""
        try:
            panel = self.query_one("#background-activity", Vertical)
            status = self.query_one("#background-status", Static)
        except NoMatches:
            return
        panel.display = bool(self._background_activity)
        status.update(
            "\n".join(f"Canvas: {text}…" for text in self._background_activity.values())
        )

    @work(thread=True, exclusive=True, name="fetch-initial-courses")
    def _fetch_initial_course_list_worker(self) -> None:
        """Fetch the initial course list via a worker so the UI is responsive."""
        transmission_modal = TransmissionModal("Loading courses from Canvas...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)
        try:
            courses_data = self.app_state.canvas_client.get_courses()
            from coursewright.core.canvas.models import CanvasCourse

            courses = [CanvasCourse.model_validate(item) for item in courses_data]
            self.app_state.courses = courses
            self.app_state.filtered_courses = courses
            self._all_courses = courses
            # Close the loading modal BEFORE rebuilding the table: the rebuild
            # starts the grading to-do fetch, which pushes its own modal, and a
            # pop after it would close that one and strand this one.
            self.app.call_from_thread(self.app.pop_screen)
            self.app.call_from_thread(self._reload_course_table)
        except Exception as load_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Course load failed: {load_error}",
            )
            self.app.call_from_thread(
                self.notify,
                f"Course load failed: {escape(str(load_error))}",
                title="Courses Error",
                severity="error",
                timeout=10,
            )

    def _reload_course_table(self) -> None:
        """Rebuild the DataTable rows after a background course fetch."""
        table = self.query_one("#course-table", DataTable)
        table.clear(columns=True)
        self._populate_table(table)
        self._refresh_grading_todo_from_cursor()
        self._refresh_last_participation_async()
        self._refresh_average_grade_async()

    @staticmethod
    def _format_average_grade(course: object) -> str:
        """Return a human-readable average grade string or '-' when unavailable."""
        average_grade = getattr(course, "average_grade", None)
        if average_grade is None:
            return "-"
        return f"{average_grade:.1f}%"

    def _refresh_average_grade_async(self) -> None:
        """Kick off non-blocking per-course average grade refresh."""
        canvas_client = self.app_state.canvas_client
        if canvas_client is None:
            return
        if not hasattr(canvas_client, "get_course_average_grades"):
            return

        course_ids = [
            getattr(course, "id", None)
            for course in self._visible_courses
            if isinstance(getattr(course, "id", None), int)
        ]
        if not course_ids:
            return

        self._begin_background_activity(BACKGROUND_AVERAGE_GRADE_KEY, "loading average grades")
        self.run_worker(
            self._load_average_grades_for_courses(course_ids),
            name="load-average-grade",
            thread=True,
        )

    async def _load_average_grades_for_courses(
        self,
        course_ids: list[int],
    ) -> dict[int, float]:
        """Fetch average current grade for the visible course set."""
        try:
            return self.app_state.canvas_client.get_course_average_grades(course_ids=course_ids)
        except Exception:
            return {}

    def _apply_average_grade_results(self, avg_by_course_id: dict[int, float]) -> None:
        """Merge average grades into visible courses and repaint the table."""
        if not avg_by_course_id:
            return

        for collection in (self.app_state.courses, self.app_state.filtered_courses):
            for course in collection:
                course_id = getattr(course, "id", None)
                if course_id in avg_by_course_id:
                    course.average_grade = avg_by_course_id[course_id]

        table = self.query_one("#course-table", DataTable)
        prior_cursor_row = table.cursor_row
        table.clear(columns=True)
        self._populate_table(table)
        if self._visible_courses:
            safe_cursor_row = min(max(prior_cursor_row, 0), len(self._visible_courses) - 1)
            table.move_cursor(row=safe_cursor_row)
        self._refresh_grading_todo_from_cursor()

    def _refresh_last_participation_async(self) -> None:
        """Kick off non-blocking per-course participation date refresh."""
        canvas_client = self.app_state.canvas_client
        if canvas_client is None:
            return
        if not hasattr(canvas_client, "get_last_student_participation_by_course"):
            return

        course_ids = [
            getattr(course, "id", None)
            for course in self._visible_courses
            if isinstance(getattr(course, "id", None), int)
        ]
        if not course_ids:
            return

        self._begin_background_activity(
            BACKGROUND_PARTICIPATION_KEY, "loading last participation dates"
        )
        self.run_worker(
            self._load_last_participation_for_courses(course_ids),
            name="load-last-participation",
            thread=True,
        )

    async def _load_last_participation_for_courses(
        self,
        course_ids: list[int],
    ) -> dict[int, str]:
        """Fetch latest student participation dates for the visible course set."""
        try:
            return self.app_state.canvas_client.get_last_student_participation_by_course(
                course_ids=course_ids
            )
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------
    def _populate_table(self, table: DataTable) -> None:
        """Build table columns and rows from app_state.filtered_courses."""
        # Define columns
        table.add_column("#", width=5)
        table.add_column("Code", width=12)
        table.add_column("Name", width=35)
        table.add_column("Term", width=18)
        table.add_column("Students", width=10)
        table.add_column("Last Part.", width=12)
        table.add_column("Avg Grade", width=10)
        table.add_column("Status", width=12)

        # Populate rows
        self._visible_courses = []
        for idx, course in enumerate(self.app_state.filtered_courses, start=1):
            self._visible_courses.append(course)
            row_data = self._format_course_row(idx, course)
            table.add_row(*row_data)

        # Every code path that rebuilds the table (initial load, filters,
        # semester change, refresh, background grade/participation repaints)
        # funnels through here, so this keeps the hint in sync everywhere.
        self._update_course_selection_hint()

    @classmethod
    def _build_course_selection_hint_label(cls, visible_course_count: int) -> str:
        """Describe which number keys select a course for the visible row count.

        Examples: 0 rows -> "" (hint hidden), 1 row -> "1 Select course",
        4 rows -> "1-4 Select course", 12 rows -> "1-9 Select course".
        """
        highest_usable_number_key = min(
            visible_course_count, cls.MAXIMUM_NUMBER_KEY_SELECTABLE_COURSES
        )
        if highest_usable_number_key <= 0:
            return ""
        if highest_usable_number_key == 1:
            return "1 Select course"
        return f"1-{highest_usable_number_key} Select course"

    def _update_course_selection_hint(self) -> None:
        """Refresh the "select-range" shortcut label to match visible rows."""
        try:
            selection_hint_link = self.query_one("#shortcut-select-range", ShortcutLink)
        except NoMatches:
            return
        hint_label = self._build_course_selection_hint_label(len(self._visible_courses))
        selection_hint_link.update(hint_label)
        selection_hint_link.display = bool(hint_label)

    def _format_course_row(self, idx: int, course) -> tuple:
        """Format a single course into table row cells."""
        # Index
        index_str = str(idx)

        # Code
        code = self._safe_str(course.course_code, "-")

        # Name (truncate if needed)
        name = self._safe_str(course.name, "Unknown Course")
        if len(name) > 35:
            name = name[:32] + "..."

        # Term
        term = self._extract_term_name(course.term)

        # Students
        students = str(course.total_students or 0)

        # Last participation date
        last_participation = self._format_last_participation_date(course)

        # Average grade
        avg_grade = self._format_average_grade(course)

        # Status
        status = self._format_status(course.workflow_state)

        return (index_str, code, name, term, students, last_participation, avg_grade, status)

    # ------------------------------------------------------------------
    # Sidebar content builders
    # ------------------------------------------------------------------
    def _build_filter_panel_text(self) -> str:
        """Generate Rich markup for the active filters panel."""
        state = self.app_state
        lines = [
            "[bold blue]Active Filters[/bold blue]",
            "",
            f"[yellow]Semester:[/yellow] {state.current_semester}",
            f"[yellow]Status:[/yellow] {state.filter_status.title()}",
        ]
        if state.filter_course_number:
            lines.append(f"[yellow]Course #:[/yellow] {escape(state.filter_course_number)}")
        if state.filter_term:
            lines.append(f"[yellow]Term Text:[/yellow] {escape(state.filter_term)}")
        lines.append("")
        lines.append(
            f"[dim]Showing {len(state.filtered_courses)} of {len(state.courses)} courses[/dim]"
        )
        return "\n".join(lines)

    def _build_grading_todo_placeholder(self) -> str:
        """Default panel text shown before a course row is highlighted."""
        return (
            "[bold cyan]Grading To-Do[/bold cyan]\n"
            "[dim]Select a course row to load past-due grading work.[/dim]"
        )

    @staticmethod
    def _course_label(course: object) -> str:
        """Return a compact course label for panel headings."""
        code = getattr(course, "course_code", "") or "Course"
        name = getattr(course, "name", "Unknown Course")
        return escape(f"{code} · {name}")

    def _update_grading_todo_panel(self, content: str) -> None:
        """Update the grading to-do panel text."""
        self.query_one("#grading-todo-panel", Static).update(content)

    def _refresh_grading_todo_from_cursor(self) -> None:
        """Load/render grading to-do for the currently highlighted course row."""
        # Keep the panel and the action context on the same course: this one
        # call is what re-points app_state.current_course at the highlight.
        course_under_cursor = self._sync_current_course_with_cursor()

        if not self._visible_courses:
            self._update_grading_todo_panel(
                "[bold cyan]Grading To-Do[/bold cyan]\n"
                "[dim]No courses available in this view.[/dim]"
            )
            return

        if course_under_cursor is None:
            self._update_grading_todo_panel(self._build_grading_todo_placeholder())
            return

        self._load_or_render_grading_todo(course_under_cursor)

    def _load_or_render_grading_todo(
        self,
        course: object,
        force_refresh: bool = False,
    ) -> None:
        """Render cached grading to-do immediately, then refresh in background."""
        course_id = getattr(course, "id", None)
        if course_id is None:
            return

        cache_entry = self._grading_todo_cache.get(course_id)
        now = datetime.now(UTC)
        if cache_entry and not force_refresh:
            cached_at, cached_items = cache_entry
            if now - cached_at < self._grading_todo_cache_ttl:
                self._render_grading_todo_panel(course, cached_items)
                return

        if self.app_state.canvas_client is None:
            self._update_grading_todo_panel(
                f"[bold cyan]Grading To-Do[/bold cyan]\n"
                f"[bold]{self._course_label(course)}[/bold]\n"
                "[dim]Canvas client unavailable in this session.[/dim]"
            )
            return

        self._update_grading_todo_panel(
            f"[bold cyan]Grading To-Do[/bold cyan]\n"
            f"[bold]{self._course_label(course)}[/bold]\n"
            "[dim]Loading past-due assignments...[/dim]\n"
            "[dim]Canvas API: fetching assignment metadata.[/dim]"
        )

        self._grading_todo_request_id += 1
        request_id = self._grading_todo_request_id
        self._latest_grading_todo_request_id = request_id
        self._latest_grading_todo_course_id = course_id
        self._show_grading_todo_transmission_modal(course)
        self.run_worker(
            self._load_grading_todo(course_id, request_id),
            name="load-grading-todo",
            thread=True,
        )

    def _show_grading_todo_transmission_modal(self, course: object) -> None:
        """Show the shared traffic modal while the to-do API fetch runs."""
        status_text = f"Fetching grading to-do from Canvas: {self._course_label(course)}..."
        if self._grading_todo_transmission_modal is not None:
            self._grading_todo_transmission_modal.update_status_text(status_text)
            return

        transmission_modal = TransmissionModal(status_text)
        self._grading_todo_transmission_modal = transmission_modal
        self.app.push_screen(transmission_modal)

    def _dismiss_grading_todo_transmission_modal(self) -> None:
        """Close the grading to-do traffic modal after the latest fetch succeeds."""
        transmission_modal = self._grading_todo_transmission_modal
        if transmission_modal is None:
            return

        self._grading_todo_transmission_modal = None
        with suppress(Exception):
            if self.app.screen is transmission_modal:
                self.app.pop_screen()

    def _mark_grading_todo_transmission_error(self, error: str) -> None:
        """Leave the traffic modal open with an error message for review."""
        transmission_modal = self._grading_todo_transmission_modal
        self._grading_todo_transmission_modal = None
        if transmission_modal is None:
            return

        with suppress(Exception):
            transmission_modal.mark_error(f"Grading to-do failed: {error}")

    async def _load_grading_todo(
        self,
        course_id: int,
        request_id: int,
    ) -> tuple[int, int, list[GradingTodoEntry], str | None]:
        """Fetch and filter assignments down to past-due items needing grading."""
        try:
            assignments = self.app_state.canvas_client.get_assignments(course_id)
        except Exception as load_error:
            return (request_id, course_id, [], str(load_error))

        now = datetime.now(UTC)
        past_due_assignments: list[tuple[object, datetime]] = []

        for assignment in assignments:
            due_at = getattr(assignment, "due_at", None)
            if due_at is None:
                continue

            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=UTC)

            if due_at >= now:
                continue

            if not getattr(assignment, "published", True):
                continue

            past_due_assignments.append((assignment, due_at))

        past_due_assignment_ids = [
            getattr(assignment, "id", None) for assignment, _due_at in past_due_assignments
        ]
        try:
            submissions = self.app_state.canvas_client.get_active_submissions_for_assignments(
                course_id,
                past_due_assignment_ids,
            )
        except Exception as load_error:
            return (request_id, course_id, [], f"Could not load grades: {load_error}")

        # A student still needs a grade when Canvas has neither a score nor a
        # grade and the instructor has not excused them.  Submission status is
        # deliberately ignored so externally graded work shows up even when
        # students never submit anything in Canvas.
        ungraded_count_by_assignment_id: dict[int, int] = {}
        for submission in submissions:
            is_excused = bool(getattr(submission, "excused", False))
            has_score = getattr(submission, "score", None) is not None
            has_grade = bool(str(getattr(submission, "grade", None) or "").strip())
            if is_excused or has_score or has_grade:
                continue
            assignment_id = getattr(submission, "assignment_id", None)
            ungraded_count_by_assignment_id[assignment_id] = (
                ungraded_count_by_assignment_id.get(assignment_id, 0) + 1
            )

        todo_entries: list[GradingTodoEntry] = []
        for assignment, due_at in past_due_assignments:
            ungraded_count = ungraded_count_by_assignment_id.get(getattr(assignment, "id", None), 0)
            if ungraded_count == 0:
                continue

            todo_entries.append(
                GradingTodoEntry(
                    assignment_name=getattr(assignment, "name", "Unnamed Assignment"),
                    due_at=due_at,
                    ungraded_count=ungraded_count,
                )
            )

        # Most recent due date first, then assignment name, so a unit's items
        # stay grouped in a predictable order.
        # Two stable sorts: name ascending, then due date descending.
        todo_entries.sort(key=lambda item: item.assignment_name.casefold())
        todo_entries.sort(key=lambda item: item.due_at, reverse=True)
        return (request_id, course_id, todo_entries, None)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Render grading to-do worker results and ignore stale responses."""
        worker = event.worker
        if worker.name == "load-last-participation":
            if event.state in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
                self._end_background_activity(BACKGROUND_PARTICIPATION_KEY)
            if event.state != WorkerState.SUCCESS:
                return
            result = worker.result
            if not isinstance(result, dict):
                return
            self._apply_last_participation_results(result)
            return

        if worker.name == "load-average-grade":
            if event.state in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
                self._end_background_activity(BACKGROUND_AVERAGE_GRADE_KEY)
            if event.state != WorkerState.SUCCESS:
                return
            result = worker.result
            if not isinstance(result, dict):
                return
            self._apply_average_grade_results(result)
            return

        if worker.name != "load-grading-todo":
            return
        if event.state != WorkerState.SUCCESS:
            return

        result = worker.result
        if not isinstance(result, tuple) or len(result) != 4:
            return

        request_id, course_id, todo_entries, error = result
        if request_id != self._latest_grading_todo_request_id:
            return

        # Stale-result guard.  A slow Canvas response for course A must never
        # repaint the panel once the highlight has moved on to course B, so
        # results are matched against the *current* highlight rather than
        # whatever course happened to be selected when the request went out.
        course = self._course_under_cursor()
        if course is None or getattr(course, "id", None) != course_id:
            return

        if error:
            self._mark_grading_todo_transmission_error(error)
            self._update_grading_todo_panel(
                f"[bold cyan]Grading To-Do[/bold cyan]\n"
                f"[bold]{self._course_label(course)}[/bold]\n"
                f"[red]Failed to load assignments:[/red] {escape(error)}"
            )
            return

        self._dismiss_grading_todo_transmission_modal()
        self._grading_todo_cache[course_id] = (datetime.now(UTC), todo_entries)
        self._render_grading_todo_panel(course, todo_entries)

    def _apply_last_participation_results(self, latest_by_course_id: dict[int, str]) -> None:
        """Merge participation dates into visible courses and repaint the table."""
        if not latest_by_course_id:
            return

        for collection in (self.app_state.courses, self.app_state.filtered_courses):
            for course in collection:
                course_id = getattr(course, "id", None)
                if course_id in latest_by_course_id:
                    course.last_participation_at = latest_by_course_id[course_id]

        table = self.query_one("#course-table", DataTable)
        prior_cursor_row = table.cursor_row
        table.clear(columns=True)
        self._populate_table(table)
        if self._visible_courses:
            safe_cursor_row = min(max(prior_cursor_row, 0), len(self._visible_courses) - 1)
            table.move_cursor(row=safe_cursor_row)
        self._refresh_grading_todo_from_cursor()

    def _render_grading_todo_panel(
        self,
        course: object,
        todo_entries: list[GradingTodoEntry],
    ) -> None:
        """Render grading to-do summary with top rows for the selected course."""
        lines = [
            "[bold cyan]Grading To-Do[/bold cyan]",
            f"[bold]{self._course_label(course)}[/bold]",
            "",
        ]

        if not todo_entries:
            lines.append("[green]Caught up:[/green] no past-due grading tasks.")
            lines.append("[dim]Press g to open NetAcad Import.[/dim]")
            self._update_grading_todo_panel("\n".join(lines))
            return

        total_assignments = len(todo_entries)
        total_ungraded = sum(item.ungraded_count for item in todo_entries)
        lines.append(
            f"[yellow]{total_assignments} assignment(s), "
            f"{total_ungraded} student(s) without grades[/yellow]"
        )
        lines.append("")

        # Render every past-due entry. The surrounding VerticalScroll
        # container caps visible height and provides scrolling, so we
        # do not clip the list to a fixed top-N preview.
        local_now = datetime.now().astimezone()
        for item in todo_entries:
            due_local = item.due_at.astimezone(local_now.tzinfo)
            overdue_days = max((local_now - due_local).days, 0)
            overdue_label = "<1d" if overdue_days == 0 else f"{overdue_days}d"
            assignment_name = item.assignment_name
            if len(assignment_name) > 28:
                assignment_name = assignment_name[:25] + "..."
            lines.append(
                f"[cyan]{due_local.strftime('%m/%d %H:%M')}[/cyan] "
                f"{escape(assignment_name)} "
                f"[yellow]{item.ungraded_count} ungraded[/yellow] "
                f"[red]{overdue_label} late[/red]"
            )

        lines.append("[dim]Press g to grade · z for zero-ungraded workflow.[/dim]")
        self._update_grading_todo_panel("\n".join(lines))

    def _compose_shortcut_buttons(self) -> ComposeResult:
        """Build grouped shortcut buttons for mouse-friendly navigation."""
        navigation_buttons = [
            ("1-9 Select course", "select-range"),
        ]
        filter_buttons = [
            ("t Semester", "select-semester"),
            ("f Filter term", "filter-term"),
            ("n Course #", "filter-course-number"),
            ("s Status", "filter-status"),
            ("c Clear filters", "clear-filters"),
        ]
        tool_buttons = [
            ("d Discussions", "open-discussions"),
            ("g NetAcad Import", "open-gradeliner"),
            ("y zyBooks Import", "open-zybooks"),
            ("b Commenter", "open-commenter"),
            ("p PT Checker", "open-packet-tracer"),
            ("a Attendance", "open-attendance"),
            ("m Model", "change-model"),
            ("z Zero ungraded", "open-zero-ungraded"),
            ("w Course design", "open-course-design"),
        ]
        system_buttons = [
            ("r Refresh", "refresh-courses"),
            ("Esc Back", "go-back"),
            ("q Quit", "request-quit"),
        ]

        shortcut_groups = [
            ("Navigation", navigation_buttons),
            ("Filters", filter_buttons),
            ("Tools", tool_buttons),
            ("System", system_buttons),
        ]

        for group_title, buttons in shortcut_groups:
            with Vertical(classes="shortcut-group"):
                yield Static(group_title, classes="shortcut-group-title")
                for button_label, button_action in buttons:
                    yield ShortcutLink(
                        button_label,
                        id=f"shortcut-{button_action}",
                    )

    # ------------------------------------------------------------------
    # Helpers for formatting course data
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_str(value, default: str = "") -> str:
        """Safely convert value to string, returning default if falsy."""
        return str(value) if value else default

    @staticmethod
    def _extract_term_name(term) -> str:
        """Extract term name from dict or string, or return 'Unknown'."""
        if not term:
            return "Unknown"
        if isinstance(term, dict):
            return term.get("name", "Unknown")
        return str(term)

    @staticmethod
    def _format_status(workflow_state: str | None) -> str:
        """Format workflow state as 'Active' or titlecased value."""
        if not workflow_state:
            return "Unknown"
        if workflow_state == "available":
            return "Active"
        return workflow_state.title()

    @staticmethod
    def _coerce_canvas_datetime(value: object) -> datetime | None:
        """Parse a Canvas datetime value that may be datetime or ISO-8601 text."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value
        if not isinstance(value, str):
            return None
        normalized_value = value.replace("Z", "+00:00")
        try:
            parsed_value = datetime.fromisoformat(normalized_value)
        except ValueError:
            return None
        if parsed_value.tzinfo is None:
            return parsed_value.replace(tzinfo=UTC)
        return parsed_value

    def _format_last_participation_date(self, course: object) -> str:
        """Return latest participation date (YYYY-MM-DD) or '-' when unknown."""
        latest_participation = self._coerce_canvas_datetime(
            getattr(course, "last_participation_at", None)
        )

        enrollments = getattr(course, "enrollments", None)
        if isinstance(enrollments, list):
            for enrollment in enrollments:
                if not isinstance(enrollment, dict):
                    continue
                candidate_participation = self._coerce_canvas_datetime(
                    enrollment.get("last_activity_at")
                )
                if candidate_participation is None:
                    continue
                if latest_participation is None or candidate_participation > latest_participation:
                    latest_participation = candidate_participation

        if latest_participation is None:
            return "-"

        return latest_participation.astimezone().strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # Selection and navigation actions
    # ------------------------------------------------------------------
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row click selection."""
        # Get the row index (0-based in DataTable)
        row_index = event.cursor_row
        self._handle_course_selection(row_index)

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        """Adopt the newly highlighted row as the selected course.

        This is what makes a single click or arrow-key move establish course
        context for the Grading To-Do panel *and* every Tools shortcut.
        """
        self._refresh_grading_todo_from_cursor()

    def action_select_course(self, index: int) -> None:
        """Handle number key course selection (0-based index)."""
        self._handle_course_selection(index)

    def on_shortcut_link_clicked(self, event: ShortcutLink.Clicked) -> None:
        """Map shortcut link clicks to the same actions as key bindings."""
        button_id = event.link.id or ""
        if not button_id.startswith("shortcut-"):
            return

        button_action = button_id.replace("shortcut-", "", 1)

        if button_action == "select-range":
            self.action_select_course(0)
            return

        shortcut_action_handlers: dict[str, Callable[[], None]] = {
            "select-semester": self.action_select_semester,
            "filter-term": self.action_filter_term,
            "filter-course-number": self.action_filter_course_number,
            "filter-status": self.action_filter_status,
            "clear-filters": self.action_clear_filters,
            "open-discussions": self.action_open_discussions,
            "open-gradeliner": self.action_open_gradeliner,
            "open-zybooks": self.action_open_zybooks,
            "open-commenter": self.action_open_commenter,
            "open-packet-tracer": self.action_open_packet_tracer,
            "open-attendance": self.action_open_attendance,
            "change-model": self.action_change_model,
            "open-zero-ungraded": self.action_open_zero_ungraded,
            "open-course-design": self.action_open_course_design,
            "refresh-courses": self.action_refresh_courses,
            "go-back": self.action_go_back,
            "request-quit": self.action_request_quit,
        }

        button_handler = shortcut_action_handlers.get(button_action)
        if button_handler:
            button_handler()

    def _course_under_cursor(self) -> object | None:
        """Return the course on the highlighted DataTable row, or ``None``.

        The highlighted row is the *single authoritative selection* for this
        whole screen.  The Grading To-Do panel, its actions, and every
        clickable/keyboard Tools shortcut read the course from here, so a
        plain single click or one arrow-key move is enough to establish
        course context.  Enter/double-click stays an opener, not the only
        way to select.

        Returns ``None`` when no row can be highlighted, which happens when
        the visible list is empty (every course filtered out, or courses not
        loaded yet).
        """
        if not self._visible_courses:
            return None
        table = self.query_one("#course-table", DataTable)
        row_index = table.cursor_row
        if row_index < 0:
            # Freshly rebuilt tables report no cursor yet; the first row is
            # what the user sees highlighted, so treat it as the selection.
            row_index = 0
        if not (0 <= row_index < len(self._visible_courses)):
            return None
        return self._visible_courses[row_index]

    def _sync_current_course_with_cursor(self) -> object | None:
        """Point ``app_state.current_course`` at the highlighted row.

        Called whenever the highlight moves and after every table rebuild
        (filter, refresh, background column updates).  Because the cursor
        always wins, a filter that hides the previously opened course cannot
        leave a stale course behind for the Tools shortcuts to open, and an
        empty list clears the context instead of silently pointing actions
        at a course the user can no longer see.

        Returns the course now selected, or ``None`` when nothing is visible.
        """
        course_under_cursor = self._course_under_cursor()
        self.app_state.current_course = course_under_cursor
        return course_under_cursor

    def _resolve_current_course(self) -> bool:
        """Ensure ``app_state.current_course`` matches the highlighted row.

        The highlighted row beats any course opened earlier, so highlighting
        course B after opening course A retargets every shortcut at B without
        requiring Enter/double-click first.

        Returns True when a course is now set, False if the list is empty.
        """
        return self._sync_current_course_with_cursor() is not None

    def _handle_course_selection(self, index: int) -> None:
        """Select a course by index and update app_state."""
        if 0 <= index < len(self._visible_courses):
            course = self._visible_courses[index]
            self.app_state.current_course = course
            # Highlight the selected row in the table
            table = self.query_one("#course-table", DataTable)
            table.move_cursor(row=index)
            # Show a brief hint so the user knows what to do next
            self.notify(
                f"Selected: {escape(str(course.name))}  —  press d·g·b·p·a·z·w to navigate",
                title="Course Selected",
                timeout=4,
            )
            self._load_or_render_grading_todo(course, force_refresh=True)

    def _open_tool_screen(self, tool_name: str) -> None:
        """Push the workflow screen for ``tool_name`` for the highlighted course.

        When that screen has not been ported yet the user is told so
        instead of the key silently doing nothing.
        """
        if not self._resolve_current_course():
            return  # No courses to select
        screen_class = find_tool_screen_class(tool_name)
        if screen_class is None:
            self.notify(
                f"The {tool_name} screen is not available in this build yet.",
                title="Coming soon",
                severity="warning",
            )
            return
        self.app.push_screen(screen_class(self.app_state))

    def action_open_discussions(self) -> None:
        """Push discussion list screen for the selected course."""
        # Route through the shared resolver so Discussions honours the
        # highlighted row exactly like every other Tools shortcut.
        self._open_tool_screen("discussions")

    def action_open_gradeliner(self) -> None:
        """Push GradeLiner screen for the selected course."""
        self._open_tool_screen("gradeliner")

    def action_open_commenter(self) -> None:
        """Push bulk commenter screen for the selected course."""
        self._open_tool_screen("commenter")

    def action_open_zybooks(self) -> None:
        """Open the file-import wizard for the selected Canvas course."""
        self._open_tool_screen("zybooks")

    def action_open_packet_tracer(self) -> None:
        """Push Packet Tracer lab checker screen for the selected course."""
        self._open_tool_screen("packet tracer")

    def action_open_zero_ungraded(self) -> None:
        """Push the Zero Ungraded screen for the selected course."""
        self._open_tool_screen("zero ungraded")

    def action_open_course_design(self) -> None:
        """Push the five-phase course-design flow for the selected course."""
        self._open_tool_screen("course design")

    def action_change_model(self) -> None:
        """Open the model picker screen so the user can change the default LLM."""
        from coursewright.ui.tui.screens.model_picker import ModelPickerScreen

        def _handle_model_selected(model_name: str | None) -> None:
            if model_name:
                self.app.notify(
                    f"Default model set to: {model_name}",
                    title="Model Updated",
                )

        self.app.push_screen(
            ModelPickerScreen(force_selection=True),
            callback=_handle_model_selected,
        )

    def action_open_attendance(self) -> None:
        """Start loading attendance data for the selected course."""
        if not self._resolve_current_course():
            return
        if find_tool_screen_class("attendance") is None:
            self.notify(
                "The attendance screen is not available in this build yet.",
                title="Coming soon",
                severity="warning",
            )
            return
        self._load_attendance_report()

    @work(thread=True)
    def _load_attendance_report(self) -> None:
        """Background worker: fetch attendance data and push AttendanceScreen.

        Pushes a TransmissionModal immediately so the user sees live Canvas
        API traffic while the fetch runs.  Progress milestone messages are
        relayed to the modal's status line; per-page chatter is suppressed
        to avoid flicker.  On success the modal is dismissed and
        AttendanceScreen is pushed.  On failure the modal stays open in
        error state so the user can read the message before pressing Escape.
        """
        from coursewright.ta.attendance.status import check_attendance

        attendance_screen_class = find_tool_screen_class("attendance")

        transmission_modal = TransmissionModal("Loading attendance data from Canvas…")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)

        def _relay_progress(message: str) -> None:
            """Forward milestone messages to the modal status line.

            Per-page pagination messages (e.g. "page 3 (300 records)")
            and per-discussion messages are suppressed — they fire too fast
            and would thrash the status line.  High-level milestone messages
            like "Loading enrolled students…" or "Indexed 1200 submissions"
            are shown so the user knows where we are.
            """
            if "page " in message and "records)" in message:
                return
            if "Scanning discussion " in message:
                return
            self.app.call_from_thread(
                transmission_modal.update_status_text,
                escape(message),
            )

        try:
            course_id = self.app_state.current_course.id
            canvas_client = self.app_state.canvas_client
            report = check_attendance(
                canvas_client,
                course_id,
                progress_callback=_relay_progress,
            )
            # Dismiss modal then immediately push AttendanceScreen.
            # The two call_from_thread calls are queued in order on the
            # event loop, so pop always completes before push runs.
            self.app.call_from_thread(self.app.pop_screen)
            self.app.call_from_thread(
                self.app.push_screen,
                attendance_screen_class(self.app_state, report),
            )
        except Exception as load_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Attendance load failed: {load_error}",
            )

    def action_refresh_courses(self) -> None:
        """Rebuild the table from the courses already loaded and refresh filters."""
        self._apply_filters()
        # Update filter panel
        filter_panel = self.query_one("#filter-panel", Static)
        filter_panel.update(self._build_filter_panel_text())

    # ------------------------------------------------------------------
    # Filter actions
    # ------------------------------------------------------------------
    def action_select_semester(self) -> None:
        """Open the semester picker modal."""

        def _handle_semester(selected: str | None) -> None:
            if selected == "__CANCEL__":
                return

            state = self.app_state
            if selected is None:
                state.show_current_semester_only = False
            else:
                state.current_semester = selected
                state.show_current_semester_only = True

            self._apply_filters()

        self.app.push_screen(SemesterPickerScreen(), callback=_handle_semester)

    def action_filter_term(self) -> None:
        """Open the term filter input modal."""

        def _handle_term(selected: str | None) -> None:
            if selected is None:  # Cancelled
                return

            self.app_state.filter_term = selected
            self._apply_filters()

        self.app.push_screen(
            InputModal(
                prompt_text="Filter by term (e.g. 'Fall 2025'):",
                default_value=self.app_state.filter_term,
            ),
            callback=_handle_term,
        )

    def action_filter_course_number(self) -> None:
        """Open the course number filter input modal.

        When a course number is entered (e.g. 'ABC101'), the table
        shows every version of that course across all semesters,
        bypassing semester and status filters.
        """

        def _handle_course_number(selected: str | None) -> None:
            if selected is None:  # Cancelled
                return

            self.app_state.filter_course_number = selected
            self._apply_filters()

        self.app.push_screen(
            InputModal(
                prompt_text="Filter by course number (e.g. 'ABC101'):",
                default_value=self.app_state.filter_course_number,
            ),
            callback=_handle_course_number,
        )

    def action_filter_status(self) -> None:
        """Open the status filter modal."""

        def _handle_status(selected: str | None) -> None:
            if selected == "__CANCEL__":
                return

            self.app_state.filter_status = selected or "all"
            self._apply_filters()

        self.app.push_screen(StatusPickerScreen(), callback=_handle_status)

    def action_clear_filters(self) -> None:
        """Reset all filters."""
        state = self.app_state
        state.filter_term = ""
        state.filter_course_number = ""
        state.filter_status = "active"
        state.show_current_semester_only = True
        state.current_semester = detect_current_semester()
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Filter the course list and update the DataTable."""
        state = self.app_state
        filtered: list = []

        # When filtering by course number, bypass semester/status filters
        # so the user can see ALL previous versions of that course.
        filtering_by_course_number = bool(state.filter_course_number)

        for course in self._all_courses:
            # Get term name for filtering
            term_name = self._extract_term_name(course.term)

            # Course number filter — match against course_code
            if filtering_by_course_number:
                course_code = self._safe_str(course.course_code, "")
                if state.filter_course_number.lower() not in course_code.lower():
                    continue
                # Skip semester/status/student filters when searching
                # by course number — the whole point is to see every
                # version across all terms.
                filtered.append(course)
                continue

            # Filter by selected semester if enabled
            if state.show_current_semester_only and not semester_matches_term(
                state.current_semester, term_name
            ):
                continue

            # Filter by term text
            if state.filter_term and state.filter_term.lower() not in term_name.lower():
                continue

            # Status filter
            is_past_semester = (
                state.show_current_semester_only
                and state.current_semester != detect_current_semester()
            )

            if not is_past_semester and (
                (state.filter_status == "active" and course.workflow_state != "available")
                or (state.filter_status == "completed" and course.workflow_state != "completed")
            ):
                continue

            # Only require students for the current semester
            if (
                state.show_current_semester_only
                and not is_past_semester
                and course.total_students == 0
            ):
                continue

            filtered.append(course)

        # Sort by course name
        filtered.sort(key=lambda x: x.name)
        state.filtered_courses = filtered

        # Update the table
        # _populate_table adds the column headings itself, so they must be
        # cleared too or every filter change would stack another set.
        table = self.query_one("#course-table", DataTable)
        table.clear(columns=True)
        self._populate_table(table)
        self._refresh_grading_todo_from_cursor()
        self._refresh_last_participation_async()
        self._refresh_average_grade_async()

        # Update filter panel
        filter_panel = self.query_one("#filter-panel", Static)
        filter_panel.update(self._build_filter_panel_text())
