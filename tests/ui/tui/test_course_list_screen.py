"""Tests for the CourseListScreen TUI screen.

These use Textual's Pilot testing framework to drive the screen
headlessly and verify table population, selection, and navigation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event
from unittest.mock import Mock

import pytest
from textual.app import App, ComposeResult
from textual.screen import Screen

from coursewright.core.canvas.models import Assignment, CanvasCourse, Submission
from coursewright.ui.tui.screens.course_list import (
    TOOL_SCREEN_LOCATIONS,
    CourseListScreen,
    GradingTodoEntry,
    ShortcutLink,
    find_tool_screen_class,
)
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

# ======================================================================
# Test app that hosts CourseListScreen
# ======================================================================


class CourseListTestApp(App):
    """Minimal app that immediately pushes a CourseListScreen."""

    def __init__(self, app_state: AppState | None = None) -> None:
        super().__init__()
        self.app_state = app_state or AppState()

    def compose(self) -> ComposeResult:
        # No widgets in the base app; CourseListScreen will be pushed on mount
        return []

    def on_mount(self) -> None:
        """Push the course list screen as soon as the app is mounted."""
        self.push_screen(CourseListScreen(self.app_state))


# ======================================================================
# Test helpers
# ======================================================================


def create_mock_course(
    course_id: int,
    name: str,
    code: str = "TEST-101",
    term_name: str = "Fall 2024",
    students: int = 10,
    workflow_state: str = "available",
    last_participation_at: datetime | None = None,
    enrollments: list[dict] | None = None,
    average_grade: float | None = None,
) -> CanvasCourse:
    """Create a mock CanvasCourse for testing."""
    return CanvasCourse(
        id=course_id,
        name=name,
        course_code=code,
        term={"name": term_name},
        total_students=students,
        workflow_state=workflow_state,
        last_participation_at=last_participation_at,
        enrollments=enrollments or [],
        average_grade=average_grade,
    )


class MockCanvasClient:
    """Small Canvas client fake exposing course assignments and submissions."""

    def __init__(
        self,
        assignments_by_course: dict[int, list[Assignment]],
        submissions_by_assignment: dict[int, list[Submission]] | None = None,
    ) -> None:
        self.assignments_by_course = assignments_by_course
        self.submissions_by_assignment = submissions_by_assignment or {}
        self.assignment_call_count = 0
        self.bulk_submission_requests: list[tuple[int, list[int]]] = []

    def get_assignments(self, course_id: int) -> list[Assignment]:
        self.assignment_call_count += 1
        return self.assignments_by_course.get(course_id, [])

    def get_active_submissions_for_assignments(
        self,
        course_id: int,
        assignment_ids: list[int],
    ) -> list[Submission]:
        self.bulk_submission_requests.append((course_id, list(assignment_ids)))
        return [
            submission
            for assignment_id in assignment_ids
            for submission in self.submissions_by_assignment.get(assignment_id, [])
        ]


class BlockingMockCanvasClient(MockCanvasClient):
    """Canvas fake that pauses get_assignments until the test releases it."""

    def __init__(
        self,
        assignments_by_course: dict[int, list[Assignment]],
        started_event: Event,
        release_event: Event,
    ) -> None:
        super().__init__(assignments_by_course)
        self.started_event = started_event
        self.release_event = release_event

    def get_assignments(self, course_id: int) -> list[Assignment]:
        self.started_event.set()
        self.release_event.wait(timeout=2)
        return super().get_assignments(course_id)


@pytest.mark.asyncio
async def test_zybooks_tool_routes_selected_course_without_canvas_calls(monkeypatch):
    """The shortcut opens the CSV wizard, not a browser or retrieval probe."""
    patch_tool_screens(monkeypatch)
    state = AppState()
    course = create_mock_course(42, "CSV Import Course")
    state.filtered_courses = [course]
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        assert "zyBooks Import" in str(screen.query_one("#shortcut-open-zybooks").render())
        assert "NetAcad Import" in str(screen.query_one("#shortcut-open-gradeliner").render())
        await pilot.press("y")
        await pilot.pause()
        assert isinstance(app.screen, RecordingToolScreen)
        assert app.screen.app_state.current_course.id == 42


@pytest.mark.asyncio
async def test_zybooks_tool_does_not_open_without_a_course(monkeypatch):
    patch_tool_screens(monkeypatch)
    state = AppState()
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.action_open_zybooks()
        await pilot.pause()
        assert isinstance(app.screen, CourseListScreen)


@pytest.mark.asyncio
async def test_tool_shortcut_says_so_when_its_screen_is_not_ported_yet(monkeypatch):
    """A key for a screen that has not landed explains itself, not a silent no-op.

    Every Tools screen is ported now, so the test points one shortcut at
    a module that does not exist to keep the fallback covered.
    """
    monkeypatch.setitem(
        TOOL_SCREEN_LOCATIONS,
        "discussions",
        ("coursewright.ui.tui.screens.not_a_real_screen", "MissingScreen"),
    )
    state = AppState()
    state.filtered_courses = [create_mock_course(42, "Any Course")]
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        await pilot.press("d")
        await pilot.pause()
        assert app.screen is screen
        notifications = [str(note.message) for note in app._notifications]
        assert any("not available in this build yet" in text for text in notifications)


@pytest.mark.parametrize("tool_name", sorted(TOOL_SCREEN_LOCATIONS))
def test_every_tools_shortcut_resolves_to_its_ported_screen(tool_name):
    """No Tools shortcut falls back to "not available in this build yet"."""
    module_name, class_name = TOOL_SCREEN_LOCATIONS[tool_name]
    screen_class = find_tool_screen_class(tool_name)
    assert screen_class is not None, f"{tool_name} has no screen"
    assert screen_class.__name__ == class_name
    assert screen_class.__module__ == module_name


async def wait_for_course_list_screen(app: CourseListTestApp, pilot) -> CourseListScreen:
    """Wait for transient modals to close and return the course-list screen."""
    for _attempt_index in range(40):
        if isinstance(app.screen, CourseListScreen):
            return app.screen
        await pilot.pause(0.05)
    raise AssertionError(f"Expected CourseListScreen, got {type(app.screen).__name__}")


# ======================================================================
# CourseListScreen tests
# ======================================================================


@pytest.mark.asyncio
async def test_course_list_screen_renders():
    """CourseListScreen should mount without errors."""
    state = AppState()
    app = CourseListTestApp(state)
    async with app.run_test():
        # Verify the screen is the active screen
        from coursewright.ui.tui.screens.course_list import CourseListScreen

        assert isinstance(app.screen, CourseListScreen)


@pytest.mark.asyncio
async def test_course_list_empty_table():
    """CourseListScreen should handle empty course list gracefully."""
    state = AppState()
    state.filtered_courses = []
    app = CourseListTestApp(state)
    async with app.run_test():
        # Table should exist even if empty
        from textual.widgets import DataTable

        screen = app.screen
        table = screen.query_one(DataTable)
        assert table is not None
        # Should have headers but no rows
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_course_list_populates_table():
    """CourseListScreen should populate table with courses from AppState."""
    state = AppState()
    state.filtered_courses = [
        create_mock_course(1, "Introduction to Programming"),
        create_mock_course(2, "Data Structures"),
        create_mock_course(3, "Algorithms"),
    ]
    app = CourseListTestApp(state)
    async with app.run_test():
        from textual.widgets import DataTable

        screen = app.screen
        table = screen.query_one(DataTable)
        # Should have 3 rows (one per course)
        assert table.row_count == 3


@pytest.mark.asyncio
async def test_course_list_truncates_long_names():
    """Course names longer than 35 chars should be truncated with ellipsis."""
    state = AppState()
    long_name = "A" * 50  # 50 character name
    state.filtered_courses = [
        create_mock_course(1, long_name),
    ]
    app = CourseListTestApp(state)
    async with app.run_test():
        from textual.widgets import DataTable

        screen = app.screen
        table = screen.query_one(DataTable)
        # Get the first row data by iterating rows
        first_row_key = list(table.rows.keys())[0] if table.rows else None
        assert first_row_key is not None
        row_data = table.get_row(first_row_key)
        # Third column is the name (after # and Code)
        name_in_table = str(row_data[2])
        # Should be truncated to 32 chars + "..."
        assert len(name_in_table) == 35
        assert name_in_table.endswith("...")


@pytest.mark.asyncio
async def test_course_list_displays_status():
    """Workflow state should be formatted correctly in status column."""
    state = AppState()
    state.filtered_courses = [
        create_mock_course(1, "Active Course", workflow_state="available"),
        create_mock_course(2, "Completed Course", workflow_state="completed"),
    ]
    app = CourseListTestApp(state)
    async with app.run_test():
        from textual.widgets import DataTable

        screen = app.screen
        table = screen.query_one(DataTable)
        # Get row keys
        row_keys = list(table.rows.keys())
        assert len(row_keys) >= 2
        # Check first row (Active)
        row1_data = table.get_row(row_keys[0])
        assert str(row1_data[7]) == "Active"
        # Check second row (Completed)
        row2_data = table.get_row(row_keys[1])
        assert str(row2_data[7]) == "Completed"


@pytest.mark.asyncio
async def test_course_list_displays_last_participation_date_from_course_field():
    """Last Part. column should render course-level last_participation_at."""
    state = AppState()
    state.filtered_courses = [
        create_mock_course(
            1,
            "Participation Course",
            last_participation_at=datetime(2026, 5, 3, 15, 30, tzinfo=UTC),
        )
    ]
    app = CourseListTestApp(state)
    async with app.run_test():
        from textual.widgets import DataTable

        screen = app.screen
        table = screen.query_one(DataTable)
        row_key = list(table.rows.keys())[0]
        row_data = table.get_row(row_key)
        assert str(row_data[5]) == "2026-05-03"


@pytest.mark.asyncio
async def test_course_list_uses_enrollment_last_activity_fallback():
    """When course field is missing, Last Part. should use enrollment activity."""
    state = AppState()
    state.filtered_courses = [
        create_mock_course(
            1,
            "Enrollment Fallback Course",
            enrollments=[{"last_activity_at": "2026-04-28T12:15:00Z"}],
        )
    ]
    app = CourseListTestApp(state)
    async with app.run_test():
        from textual.widgets import DataTable

        screen = app.screen
        table = screen.query_one(DataTable)
        row_key = list(table.rows.keys())[0]
        row_data = table.get_row(row_key)
        assert str(row_data[5]) == "2026-04-28"


@pytest.mark.asyncio
async def test_course_list_displays_average_grade():
    """Avg Grade column (index 6) should render formatted percentage or '-'."""
    state = AppState()
    state.filtered_courses = [
        create_mock_course(1, "Graded Course", average_grade=83.456),
        create_mock_course(2, "Ungraded Course"),
    ]
    app = CourseListTestApp(state)
    async with app.run_test():
        from textual.widgets import DataTable

        screen = app.screen
        table = screen.query_one(DataTable)
        row_keys = list(table.rows.keys())
        assert len(row_keys) >= 2
        row1_data = table.get_row(row_keys[0])
        assert str(row1_data[6]) == "83.5%"
        row2_data = table.get_row(row_keys[1])
        assert str(row2_data[6]) == "-"


@pytest.mark.asyncio
async def test_course_list_row_selection():
    """Clicking a row should set current_course in AppState."""
    state = AppState()
    course1 = create_mock_course(1, "Course One")
    course2 = create_mock_course(2, "Course Two")
    state.filtered_courses = [course1, course2]
    app = CourseListTestApp(state)
    original_open_discussions_action = CourseListScreen.action_open_discussions

    def stub_open_discussions_action(self: CourseListScreen) -> None:
        """Prevent screen push side effects for this focused selection test."""
        return None

    CourseListScreen.action_open_discussions = stub_open_discussions_action
    try:
        async with app.run_test() as pilot:
            from textual.widgets import DataTable

            # Let the screen fully compose before interacting — avoids
            # intermittent NoMatches on Header internals during shutdown.
            await pilot.pause()
            # Click the first row
            await pilot.click(DataTable, offset=(1, 1))
            assert state.current_course is not None
            assert state.current_course.id == 1
    finally:
        CourseListScreen.action_open_discussions = original_open_discussions_action


@pytest.mark.asyncio
async def test_course_list_number_key_selection():
    """Pressing number keys 1-9 should select corresponding courses."""
    state = AppState()
    state.filtered_courses = [create_mock_course(i, f"Course {i}") for i in range(1, 10)]
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        # Press "3" to select the third course
        await pilot.press("3")
        # Current course should be course ID 3
        assert state.current_course is not None
        assert state.current_course.id == 3


# ======================================================================
# UX-9: number-key hint matches the visible course count
# ======================================================================


def read_course_selection_hint(screen: CourseListScreen) -> tuple[str, bool]:
    """Return (label text, is displayed) for the number-key selection hint."""
    selection_hint_link = screen.query_one("#shortcut-select-range", ShortcutLink)
    return str(selection_hint_link.render()), bool(selection_hint_link.display)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("visible_course_count", "expected_hint_label"),
    [
        (1, "1 Select course"),
        (4, "1-4 Select course"),
        (12, "1-9 Select course"),
    ],
)
async def test_course_selection_hint_matches_visible_course_count(
    visible_course_count: int,
    expected_hint_label: str,
):
    """The hint should advertise only the number keys that map to a row."""
    state = AppState()
    state.filtered_courses = [
        create_mock_course(course_id, f"Course {course_id}")
        for course_id in range(1, visible_course_count + 1)
    ]
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        hint_label, hint_is_displayed = read_course_selection_hint(screen)
        assert hint_label == expected_hint_label
        assert hint_is_displayed


@pytest.mark.asyncio
async def test_course_selection_hint_hidden_when_no_courses_visible():
    """With zero rows there is nothing to select, so the hint is hidden."""
    state = AppState()
    state.filtered_courses = []
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        hint_label, hint_is_displayed = read_course_selection_hint(screen)
        assert hint_label == ""
        assert not hint_is_displayed


@pytest.mark.asyncio
async def test_course_selection_hint_updates_after_filter_change():
    """Applying a filter that shrinks the table should shrink the hint too."""
    state = AppState()
    state.show_current_semester_only = False
    state.filter_status = "all"
    courses = [
        create_mock_course(1, "Intro A", code="ABC111"),
        create_mock_course(2, "Intro B", code="ABC111"),
        create_mock_course(3, "Networking", code="ABC222"),
        create_mock_course(4, "Security", code="ABC233"),
        create_mock_course(5, "Databases", code="ABC244"),
    ]
    state.courses = courses
    state.filtered_courses = courses
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        assert read_course_selection_hint(screen) == ("1-5 Select course", True)

        state.filter_course_number = "ABC111"
        screen._apply_filters()
        await pilot.pause()
        assert read_course_selection_hint(screen) == ("1-2 Select course", True)

        state.filter_course_number = "NO-SUCH-COURSE"
        screen._apply_filters()
        await pilot.pause()
        assert read_course_selection_hint(screen) == ("", False)


@pytest.mark.asyncio
async def test_number_key_beyond_visible_courses_is_harmless_no_op():
    """Pressing a number key past the last row should change nothing."""
    state = AppState()
    state.filtered_courses = [
        create_mock_course(course_id, f"Course {course_id}") for course_id in range(1, 5)
    ]
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        await pilot.press("2")
        await pilot.pause()
        assert state.current_course is not None
        assert state.current_course.id == 2

        await pilot.press("7")
        await pilot.pause()
        assert isinstance(app.screen, CourseListScreen)
        assert app.screen is screen
        assert state.current_course.id == 2


@pytest.mark.asyncio
async def test_course_list_filter_panel_content():
    """Filter panel should show current semester and status."""
    state = AppState()
    state.current_semester = "Fall 2024"
    state.filter_status = "active"
    state.courses = [create_mock_course(i, f"Course {i}") for i in range(1, 6)]
    state.filtered_courses = state.courses[:3]  # 3 of 5 shown
    app = CourseListTestApp(state)
    async with app.run_test():
        from textual.widgets import Static

        screen = app.screen
        filter_panel = screen.query_one("#filter-panel", Static)
        # Panel should contain semester and status
        content = filter_panel.render()
        content_str = str(content)
        assert "Fall 2024" in content_str
        assert "Active" in content_str
        assert "3 of 5" in content_str or "Showing 3" in content_str


@pytest.mark.asyncio
async def test_course_list_help_panel_content():
    """Help panel should show clickable shortcut links."""
    state = AppState()
    app = CourseListTestApp(state)
    async with app.run_test():
        from textual.containers import Vertical

        from coursewright.ui.tui.screens.course_list import ShortcutLink

        screen = app.screen
        help_panel = screen.query_one("#help-panel", Vertical)
        links = help_panel.query(ShortcutLink)
        # UX-2 converted help text into clickable ShortcutLink widgets
        link_labels = [str(link.render()) for link in links]
        # Should contain key shortcut links
        assert any("d" in lbl.lower() or "discussion" in lbl.lower() for lbl in link_labels), (
            f"Expected a discussions link, got: {link_labels}"
        )
        assert any("q" in lbl.lower() or "quit" in lbl.lower() for lbl in link_labels), (
            f"Expected a quit link, got: {link_labels}"
        )


@pytest.mark.asyncio
async def test_grading_todo_panel_shows_pending_past_due_items():
    """Grading to-do panel should show only past-due assignments needing grading."""
    now = datetime.now(UTC)
    state = AppState()
    course = create_mock_course(101, "Networking Fundamentals", code="ABC111")
    state.filtered_courses = [course]
    state.canvas_client = MockCanvasClient(
        {
            101: [
                Assignment(
                    id=1,
                    name="Lab 1",
                    due_at=now - timedelta(days=2),
                    needs_grading_count=4,
                    published=True,
                ),
                Assignment(
                    id=2,
                    name="Lab 2",
                    due_at=now + timedelta(days=1),
                    needs_grading_count=7,
                    published=True,
                ),
                Assignment(
                    id=3,
                    name="Lab 0",
                    due_at=now - timedelta(days=3),
                    needs_grading_count=0,
                    published=True,
                ),
                Assignment(
                    id=4,
                    name="Lab Fully Graded",
                    due_at=now - timedelta(days=4),
                    needs_grading_count=0,
                    published=True,
                ),
            ]
        },
        {
            1: [
                Submission(id=1, assignment_id=1, workflow_state="unsubmitted"),
                Submission(
                    id=2,
                    assignment_id=1,
                    workflow_state="submitted",
                    submitted_at=now - timedelta(days=1),
                ),
                Submission(id=3, assignment_id=1, score=0, grade="0"),
                Submission(id=4, assignment_id=1, score=None, grade="A"),
                Submission(id=7, assignment_id=1, excused=True),
            ],
            3: [Submission(id=5, assignment_id=3, workflow_state="unsubmitted")],
            4: [Submission(id=6, assignment_id=4, score=10, grade="10")],
        },
    )

    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import Static

        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        todo_panel = screen.query_one("#grading-todo-panel", Static)
        todo_text = str(todo_panel.render())
        max_panel_refresh_attempts = 40
        for _attempt_index in range(max_panel_refresh_attempts):
            if "Loading past-due assignments..." not in todo_text:
                break
            await pilot.pause(0.05)
            todo_text = str(todo_panel.render())
        assert "Grading To-Do" in todo_text
        assert "Lab 1" in todo_text
        assert "Lab 0" in todo_text
        assert "Lab 2" not in todo_text
        assert "Lab Fully Graded" not in todo_text
        assert "3 student(s) without grades" in todo_text
        assert "2 ungraded" in todo_text
        # Most recent due date first: Lab 1 (2 days late) before Lab 0 (3 days late).
        assert todo_text.index("Lab 1") < todo_text.index("Lab 0")
        # Each load makes one bulk request covering every past-due assignment;
        # the future assignment (id 2) is never requested.
        bulk_requests = state.canvas_client.bulk_submission_requests
        assert len(bulk_requests) == state.canvas_client.assignment_call_count
        assert all(request == (101, [1, 3, 4]) for request in bulk_requests)


@pytest.mark.asyncio
async def test_grading_todo_sorts_by_due_date_then_name():
    """Rows sort most recent due date first, then by assignment name."""
    now = datetime.now(UTC)
    shared_due_at = now - timedelta(days=4)
    state = AppState()
    state.canvas_client = MockCanvasClient(
        {
            105: [
                Assignment(id=1, name="U10A01: zyBook", due_at=shared_due_at),
                Assignment(id=2, name="U09T01: Quiz", due_at=shared_due_at),
                Assignment(id=3, name="u09A02: zyLabs", due_at=shared_due_at),
                Assignment(id=4, name="U03D01: Discussion", due_at=now - timedelta(days=25)),
            ]
        },
        {
            assignment_id: [Submission(id=assignment_id, assignment_id=assignment_id)]
            for assignment_id in range(1, 5)
        },
    )

    screen = CourseListScreen(state)
    _request_id, _course_id, todo_entries, error = await screen._load_grading_todo(105, 1)

    assert error is None
    assert [entry.assignment_name for entry in todo_entries] == [
        "u09A02: zyLabs",
        "U09T01: Quiz",
        "U10A01: zyBook",
        "U03D01: Discussion",
    ]


@pytest.mark.asyncio
async def test_grading_todo_reports_submission_loading_errors():
    """A failed grade lookup should be reported instead of hiding the task."""
    now = datetime.now(UTC)
    state = AppState()
    course = create_mock_course(104, "Networking Fundamentals", code="ABC111")
    state.filtered_courses = [course]
    state.canvas_client = MockCanvasClient(
        {
            104: [
                Assignment(
                    id=1,
                    name="External-System Grades",
                    due_at=now - timedelta(days=1),
                    published=True,
                )
            ]
        }
    )
    state.canvas_client.get_active_submissions_for_assignments = Mock(
        side_effect=RuntimeError("Canvas unavailable")
    )

    screen = CourseListScreen(state)
    result = await screen._load_grading_todo(104, 1)

    assert result[0:2] == (1, 104)
    assert result[2] == []
    assert "Could not load grades" in result[3]
    assert "Canvas unavailable" in result[3]


@pytest.mark.asyncio
async def test_grading_todo_fetch_shows_canvas_api_transmission_modal():
    """The course-list grading to-do fetch should show live API traffic."""
    now = datetime.now(UTC)
    started_event = Event()
    release_event = Event()
    state = AppState()
    course = create_mock_course(102, "Networking Fundamentals", code="ABC111")
    state.filtered_courses = [course]
    state.canvas_client = BlockingMockCanvasClient(
        {
            102: [
                Assignment(
                    id=1,
                    name="Lab 1",
                    due_at=now - timedelta(days=2),
                    needs_grading_count=4,
                    published=True,
                )
            ]
        },
        started_event,
        release_event,
    )

    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import Static

        for _attempt_index in range(40):
            if started_event.is_set() and isinstance(app.screen, TransmissionModal):
                break
            await pilot.pause(0.05)

        assert isinstance(app.screen, TransmissionModal)
        status_text = str(app.screen.query_one("#transmission-status", Static).render())
        assert "Fetching grading to-do from Canvas" in status_text
        assert "ABC111" in status_text

        release_event.set()
        for _attempt_index in range(40):
            if isinstance(app.screen, CourseListScreen):
                break
            await pilot.pause(0.05)

        assert isinstance(app.screen, CourseListScreen)


@pytest.mark.asyncio
async def test_course_selection_refreshes_cached_grading_todo():
    """Selecting a course row should re-read grading to-do from Canvas."""
    now = datetime.now(UTC)
    state = AppState()
    course = create_mock_course(103, "Networking Fundamentals", code="ABC111")
    state.filtered_courses = [course]
    canvas_client = MockCanvasClient(
        {
            103: [
                Assignment(
                    id=1,
                    name="Lab 1",
                    due_at=now - timedelta(days=2),
                    needs_grading_count=4,
                    published=True,
                )
            ]
        }
    )
    state.canvas_client = canvas_client

    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        screen = await wait_for_course_list_screen(app, pilot)
        for _attempt_index in range(40):
            if canvas_client.assignment_call_count >= 1:
                break
            await pilot.pause(0.05)

        baseline_call_count = canvas_client.assignment_call_count
        assert baseline_call_count >= 1

        screen._handle_course_selection(0)
        for _attempt_index in range(40):
            if canvas_client.assignment_call_count > baseline_call_count:
                break
            await pilot.pause(0.05)

        assert canvas_client.assignment_call_count == baseline_call_count + 1


@pytest.mark.asyncio
async def test_grading_todo_panel_shows_caught_up_message():
    """Panel should show caught-up text when no past-due grading remains."""
    now = datetime.now(UTC)
    state = AppState()
    course = create_mock_course(201, "Python Basics", code="ABC333")
    state.filtered_courses = [course]
    state.canvas_client = MockCanvasClient(
        {
            201: [
                Assignment(
                    id=11,
                    name="Week 1 Quiz",
                    due_at=now - timedelta(days=1),
                    needs_grading_count=0,
                    published=True,
                )
            ]
        },
        {
            11: [Submission(id=11, assignment_id=11, score=10, grade="10")],
        },
    )

    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import Static

        screen = await wait_for_course_list_screen(app, pilot)
        todo_panel = screen.query_one("#grading-todo-panel", Static)
        todo_text = str(todo_panel.render())
        max_panel_refresh_attempts = 40
        for _attempt_index in range(max_panel_refresh_attempts):
            if "Loading past-due assignments..." not in todo_text:
                break
            await pilot.pause(0.05)
            todo_text = str(todo_panel.render())
        assert "Caught up" in todo_text
        assert "Press g to open NetAcad Import." in todo_text


@pytest.mark.asyncio
async def test_grading_todo_panel_renders_many_items_in_scrollable_container():
    """Panel should render every past-due item even when the list exceeds the
    old fixed 12-line cap; the surrounding VerticalScroll lets the user scroll."""
    from textual.containers import VerticalScroll

    now = datetime.now(UTC)
    state = AppState()
    course = create_mock_course(301, "Heavy Grading Course", code="ABC999")
    state.filtered_courses = [course]
    pending_assignment_count = 20
    pending_assignments = [
        Assignment(
            id=index + 1,
            name=f"Assignment {index + 1:02d}",
            due_at=now - timedelta(days=index + 1),
            needs_grading_count=index + 1,
            published=True,
        )
        for index in range(pending_assignment_count)
    ]
    state.canvas_client = MockCanvasClient(
        {301: pending_assignments},
        {
            index + 1: [
                Submission(
                    id=index + 1,
                    assignment_id=index + 1,
                    workflow_state="unsubmitted",
                )
            ]
            for index in range(pending_assignment_count)
        },
    )

    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import Static

        screen = await wait_for_course_list_screen(app, pilot)
        # The Static panel must live inside a VerticalScroll so content beyond
        # the visible area is reachable.
        scroll_container = screen.query_one("#grading-todo-panel-scroll", VerticalScroll)
        assert scroll_container is not None
        todo_panel = screen.query_one("#grading-todo-panel", Static)
        todo_text = str(todo_panel.render())
        max_panel_refresh_attempts = 40
        for _attempt_index in range(max_panel_refresh_attempts):
            if "Loading past-due assignments..." not in todo_text:
                break
            await pilot.pause(0.05)
            todo_text = str(todo_panel.render())
        # Every assignment should be present in the rendered text — no silent
        # clipping at the old 12-line ceiling.
        for index in range(pending_assignment_count):
            expected_assignment_label = f"Assignment {index + 1:02d}"
            assert expected_assignment_label in todo_text, (
                f"Missing {expected_assignment_label!r} in panel text"
            )


@pytest.mark.asyncio
async def test_course_list_quit_binding():
    """Pressing 'q' should quit the application."""
    state = AppState()
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.press("q")
        # App should exit gracefully (no assertion needed, just shouldn't crash)


@pytest.mark.asyncio
async def test_course_list_escape_goes_back():
    """Pressing Escape should pop back to previous screen."""
    state = AppState()
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        # Initially on CourseListScreen
        from coursewright.ui.tui.screens.course_list import CourseListScreen

        assert isinstance(app.screen, CourseListScreen)
        # Press escape - should attempt to go back
        # Since there's no previous screen, the app will exit (caught in base class)
        await pilot.press("escape")
        # No assertion needed - just testing it doesn't crash


# ======================================================================
# Course number filter tests
# ======================================================================


@pytest.mark.asyncio
async def test_course_number_filter_matches_course_code():
    """Filtering by course number should match against course_code."""
    state = AppState()
    state.show_current_semester_only = False
    state.filter_status = "all"
    courses = [
        create_mock_course(1, "Intro to CS", code="ABC111", term_name="Fall 2024"),
        create_mock_course(2, "Intro to CS", code="ABC111", term_name="Fall 2025"),
        create_mock_course(3, "Networking", code="ABC222", term_name="Fall 2024"),
    ]
    state.courses = courses
    state.filtered_courses = courses
    app = CourseListTestApp(state)
    async with app.run_test():
        screen = app.screen
        # Manually set the course number filter and apply
        state.filter_course_number = "ABC111"
        screen._all_courses = courses
        screen._apply_filters()
        # Should show only the two ABC111 courses
        assert len(state.filtered_courses) == 2
        for course in state.filtered_courses:
            assert "ABC111" in course.course_code


@pytest.mark.asyncio
async def test_course_number_filter_is_case_insensitive():
    """Course number filter should match regardless of case."""
    state = AppState()
    state.show_current_semester_only = False
    courses = [
        create_mock_course(1, "Intro to CS", code="ABC111"),
        create_mock_course(2, "Networking", code="ABC222"),
    ]
    state.courses = courses
    state.filtered_courses = courses
    app = CourseListTestApp(state)
    async with app.run_test():
        screen = app.screen
        state.filter_course_number = "abc111"
        screen._all_courses = courses
        screen._apply_filters()
        assert len(state.filtered_courses) == 1
        assert state.filtered_courses[0].course_code == "ABC111"


@pytest.mark.asyncio
async def test_course_number_filter_bypasses_semester_and_status():
    """When filtering by course number, semester and status filters are bypassed."""
    state = AppState()
    # Set restrictive filters that would normally hide courses
    state.show_current_semester_only = True
    state.current_semester = "Spring 2026"
    state.filter_status = "active"
    courses = [
        create_mock_course(
            1, "Old CS", code="ABC111", term_name="Fall 2024",
            workflow_state="completed", students=0,
        ),
        create_mock_course(
            2, "Current CS", code="ABC111", term_name="Spring 2026",
        ),
        create_mock_course(
            3, "Old Networking", code="ABC222", term_name="Fall 2024",
            workflow_state="completed",
        ),
    ]
    state.courses = courses
    state.filtered_courses = courses
    app = CourseListTestApp(state)
    async with app.run_test():
        screen = app.screen
        state.filter_course_number = "ABC111"
        screen._all_courses = courses
        screen._apply_filters()
        # Both ABC111 courses should appear, even completed/past ones
        assert len(state.filtered_courses) == 2


@pytest.mark.asyncio
async def test_course_number_filter_cleared_by_clear_filters():
    """Pressing 'c' (clear filters) should reset course number filter."""
    state = AppState()
    state.show_current_semester_only = False
    state.filter_status = "all"
    courses = [
        create_mock_course(1, "Intro to CS", code="ABC111"),
        create_mock_course(2, "Networking", code="ABC222"),
    ]
    state.courses = courses
    state.filtered_courses = courses
    state.filter_course_number = "ABC111"
    app = CourseListTestApp(state)
    async with app.run_test():
        screen = app.screen
        screen._all_courses = courses
        screen.action_clear_filters()
        assert state.filter_course_number == ""


@pytest.mark.asyncio
async def test_course_number_filter_shows_in_filter_panel():
    """Filter panel should display the active course number filter."""
    state = AppState()
    state.filter_course_number = "ABC111"
    state.courses = [create_mock_course(1, "Course")]
    state.filtered_courses = state.courses
    app = CourseListTestApp(state)
    async with app.run_test():
        from textual.widgets import Static

        screen = app.screen
        filter_panel = screen.query_one("#filter-panel", Static)
        content_str = str(filter_panel.render())
        assert "ABC111" in content_str


# ======================================================================
# UX-8: the highlighted course is the shortcut target
#
# Repro these guard against: highlight ABC201, but clicking Discussions
# opens the previously active ABC101.  The DataTable cursor highlight is
# now the single authoritative selection, so a single click or one arrow
# key retargets the Grading To-Do panel *and* every Tools shortcut.
# ======================================================================


class RecordingToolScreen(Screen):
    """Stand-in tool screen that records the AppState it was handed.

    Tool screens (Discussions, GradeLiner, ...) all take ``app_state`` and
    read ``current_course`` from it, so recording the state object is enough
    to prove which course a shortcut actually routed to.
    """

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state

    def compose(self) -> ComposeResult:
        return []


def patch_tool_screens(monkeypatch) -> None:
    """Replace the real tool screens with recording stand-ins.

    The shortcut actions look their screen class up through
    ``find_tool_screen_class``, so patching that one function intercepts
    every push without dragging a real Canvas-backed screen into these
    tests.  ``test_course_list_navigation.py`` checks the real screens.
    """
    monkeypatch.setattr(
        "coursewright.ui.tui.screens.course_list.find_tool_screen_class",
        lambda tool_name: RecordingToolScreen,
    )


def build_two_course_state() -> AppState:
    """Two visible synthetic courses mirroring the UX-8 repro."""
    state = AppState()
    state.filtered_courses = [
        create_mock_course(11, "Sample Course A", code="ABC101.1001.F26"),
        create_mock_course(22, "Sample Course B", code="ABC201.1002.F26"),
    ]
    return state


@pytest.mark.asyncio
async def test_single_click_highlight_sets_course_context(monkeypatch):
    """A single click on a row establishes course context — no Enter needed."""
    patch_tool_screens(monkeypatch)
    state = build_two_course_state()
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import DataTable

        await pilot.pause()
        # Row 2 of the table body — the second course.
        await pilot.click(DataTable, offset=(1, 2))
        await pilot.pause()
        assert state.current_course is not None
        assert state.current_course.id == 22


@pytest.mark.asyncio
async def test_keyboard_highlight_sets_course_context():
    """Moving the cursor with the arrow keys establishes course context."""
    state = build_two_course_state()
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import DataTable

        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        screen.query_one("#course-table", DataTable).focus()
        await pilot.press("down")
        await pilot.pause()
        assert state.current_course is not None
        assert state.current_course.id == 22


@pytest.mark.asyncio
async def test_highlight_retargets_shortcut_after_earlier_course_opened(monkeypatch):
    """The UX-8 repro: highlight B after opening A, and Discussions opens B."""
    patch_tool_screens(monkeypatch)
    state = build_two_course_state()
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import DataTable

        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)

        # Course A becomes the active course the old way (number key).
        await pilot.press("1")
        await pilot.pause()
        assert state.current_course.id == 11

        # Now merely *highlight* course B.
        screen.query_one("#course-table", DataTable).focus()
        await pilot.press("down")
        await pilot.pause()

        # Every destination must follow the highlight, not the old course.
        screen.action_open_discussions()
        await pilot.pause()
        assert isinstance(app.screen, RecordingToolScreen)
        assert app.screen.app_state.current_course.id == 22


@pytest.mark.asyncio
async def test_shortcut_link_click_uses_highlighted_course(monkeypatch):
    """Clickable Tools shortcuts route to the highlighted course too."""
    patch_tool_screens(monkeypatch)
    state = build_two_course_state()
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import DataTable

        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)

        await pilot.press("1")
        await pilot.pause()
        screen.query_one("#course-table", DataTable).focus()
        await pilot.press("down")
        await pilot.pause()

        # Drive the shortcut the way a mouse click on the link does.
        shortcut_link = screen.query_one("#shortcut-open-gradeliner", ShortcutLink)
        screen.on_shortcut_link_clicked(ShortcutLink.Clicked(shortcut_link))
        await pilot.pause()
        assert isinstance(app.screen, RecordingToolScreen)
        assert app.screen.app_state.current_course.id == 22


@pytest.mark.asyncio
async def test_enter_on_row_still_opens_highlighted_course(monkeypatch):
    """Regression: Enter/double-click keeps working as an opener."""
    patch_tool_screens(monkeypatch)
    state = build_two_course_state()
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import DataTable

        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        screen.query_one("#course-table", DataTable).focus()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert state.current_course is not None
        assert state.current_course.id == 22


@pytest.mark.asyncio
async def test_filter_hiding_selected_course_retargets_context():
    """A filter that hides the selected course must not leave it selected."""
    state = AppState()
    state.show_current_semester_only = False
    state.filter_status = "all"
    courses = [
        create_mock_course(11, "Sample Course A", code="ABC101", term_name="Fall 2026"),
        create_mock_course(22, "Sample Course B", code="ABC201", term_name="Fall 2026"),
    ]
    state.courses = courses
    state.filtered_courses = courses
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        screen._all_courses = courses

        await pilot.press("1")
        await pilot.pause()
        assert state.current_course.id == 11

        # Filter down to the other course only.
        state.filter_course_number = "ABC201"
        screen._apply_filters()
        await pilot.pause()

        # The hidden course must not remain the shortcut target.
        assert state.current_course is not None
        assert state.current_course.id == 22
        assert screen._resolve_current_course() is True
        assert state.current_course.id == 22


@pytest.mark.asyncio
async def test_empty_course_list_clears_context_and_blocks_actions():
    """An empty list clears context so actions cannot fire at a hidden course."""
    state = AppState()
    state.show_current_semester_only = False
    state.filter_status = "all"
    courses = [create_mock_course(11, "Sample Course A", code="ABC101")]
    state.courses = courses
    state.filtered_courses = courses
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        screen._all_courses = courses

        await pilot.press("1")
        await pilot.pause()
        assert state.current_course.id == 11

        # Filter everything out.
        state.filter_course_number = "NOSUCHCOURSE"
        screen._apply_filters()
        await pilot.pause()

        assert state.filtered_courses == []
        assert state.current_course is None
        assert screen._resolve_current_course() is False

        # Opening a tool with nothing visible must be a no-op, not a crash.
        screen.action_open_discussions()
        await pilot.pause()
        assert isinstance(app.screen, CourseListScreen)


@pytest.mark.asyncio
async def test_stale_grading_todo_result_cannot_replace_current_panel():
    """A late response for course A must not repaint the panel for course B."""
    from types import SimpleNamespace

    from textual.worker import WorkerState

    state = build_two_course_state()
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        from textual.widgets import DataTable, Static

        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)

        # Highlight course B (ABC201).
        screen.query_one("#course-table", DataTable).focus()
        await pilot.press("down")
        await pilot.pause()
        assert state.current_course.id == 22

        # A slow worker finally answers for course A (ABC101) instead.
        screen._latest_grading_todo_request_id = 7
        stale_entry = GradingTodoEntry(
            assignment_name="Stale Lab From ABC101",
            due_at=datetime.now(UTC) - timedelta(days=1),
            ungraded_count=3,
        )
        screen.on_worker_state_changed(
            SimpleNamespace(
                state=WorkerState.SUCCESS,
                worker=SimpleNamespace(
                    name="load-grading-todo",
                    state=WorkerState.SUCCESS,
                    result=(7, 11, [stale_entry], None),
                )
            )
        )
        await pilot.pause()

        todo_text = str(screen.query_one("#grading-todo-panel", Static).render())
        assert "Stale Lab From ABC101" not in todo_text
        assert "ABC101.1001.F26" not in todo_text
        # And the context itself is untouched.
        assert state.current_course.id == 22


@pytest.mark.asyncio
async def test_applying_filters_does_not_duplicate_table_columns():
    """Each filter change rebuilds the rows, never the column headings twice."""
    from textual.widgets import DataTable

    state = AppState()
    state.show_current_semester_only = False
    state.courses = state.filtered_courses = [create_mock_course(1, "Only Course")]
    app = CourseListTestApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await wait_for_course_list_screen(app, pilot)
        table = screen.query_one("#course-table", DataTable)
        columns_before = len(table.columns)

        screen.action_clear_filters()
        await pilot.pause()

        assert len(table.columns) == columns_before
