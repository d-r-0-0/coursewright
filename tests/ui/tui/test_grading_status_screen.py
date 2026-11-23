"""Tests for the WeeklyGradingStatusScreen Textual screen."""

from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Header, LoadingIndicator, Static

from coursewright.ui.tui.screens.grading_status import WeeklyGradingStatusScreen
from coursewright.ui.tui.state import AppState
from tests.ui.tui.activity_helpers import wait_until


def _build_app_state() -> AppState:
    """Create a minimal state object for grading status screen tests."""
    app_state = AppState()
    app_state.canvas_client = MagicMock()
    app_state.current_course = SimpleNamespace(id=42, name="Test Course")
    return app_state


class GradingStatusTestApp(App):
    """Minimal host app that pushes WeeklyGradingStatusScreen on mount."""

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self._app_state = app_state

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(WeeklyGradingStatusScreen(self._app_state))


def test_get_current_week_window_starts_monday_and_ends_sunday():
    """Current week boundaries should be Monday 00:00:00 to Sunday 23:59:59."""
    fixed_now = datetime.fromisoformat("2026-03-18T15:20:00+00:00")
    with patch("coursewright.ui.tui.screens.grading_status.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        week_start, week_end = WeeklyGradingStatusScreen._get_current_week_window()

    assert week_start.weekday() == 0
    assert week_start.hour == 0
    assert week_start.minute == 0
    assert week_start.second == 0
    assert week_end.weekday() == 6
    assert week_end.hour == 23
    assert week_end.minute == 59
    assert week_end.second == 59


def test_filter_assignments_due_this_week_includes_due_dates_in_window():
    """Assignments due within the current week should be included."""
    screen = WeeklyGradingStatusScreen(_build_app_state())
    monday = datetime.fromisoformat("2026-03-16T00:00:00+00:00")
    sunday = datetime.fromisoformat("2026-03-22T23:59:59+00:00")
    due_inside = SimpleNamespace(id=1, due_at=datetime.fromisoformat("2026-03-20T12:00:00+00:00"))
    with patch.object(screen, "_get_current_week_window", return_value=(monday, sunday)):
        filtered = screen._filter_assignments_due_this_week([due_inside])

    assert filtered == [due_inside]


def test_filter_assignments_due_this_week_excludes_due_dates_outside_window():
    """Assignments outside the current week should be excluded."""
    screen = WeeklyGradingStatusScreen(_build_app_state())
    monday = datetime.fromisoformat("2026-03-16T00:00:00+00:00")
    sunday = datetime.fromisoformat("2026-03-22T23:59:59+00:00")
    due_before = SimpleNamespace(id=1, due_at=datetime.fromisoformat("2026-03-15T23:59:59+00:00"))
    due_after = SimpleNamespace(id=2, due_at=datetime.fromisoformat("2026-03-23T00:00:00+00:00"))
    with patch.object(screen, "_get_current_week_window", return_value=(monday, sunday)):
        filtered = screen._filter_assignments_due_this_week([due_before, due_after])

    assert filtered == []


def test_filter_assignments_due_this_week_excludes_none_due_dates():
    """Assignments with no due date should be skipped."""
    screen = WeeklyGradingStatusScreen(_build_app_state())
    monday = datetime.fromisoformat("2026-03-16T00:00:00+00:00")
    sunday = datetime.fromisoformat("2026-03-22T23:59:59+00:00")
    no_due_date = SimpleNamespace(id=1, due_at=None)
    with patch.object(screen, "_get_current_week_window", return_value=(monday, sunday)):
        filtered = screen._filter_assignments_due_this_week([no_due_date])

    assert filtered == []


def test_filter_assignments_due_this_week_includes_exact_start_and_end():
    """Assignments due at exact window boundaries should be included."""
    screen = WeeklyGradingStatusScreen(_build_app_state())
    monday = datetime.fromisoformat("2026-03-16T00:00:00+00:00")
    sunday = datetime.fromisoformat("2026-03-22T23:59:59+00:00")
    due_at_start = SimpleNamespace(id=1, due_at=monday)
    due_at_end = SimpleNamespace(id=2, due_at=sunday)
    with patch.object(screen, "_get_current_week_window", return_value=(monday, sunday)):
        filtered = screen._filter_assignments_due_this_week([due_at_start, due_at_end])

    assert filtered == [due_at_start, due_at_end]


def test_format_checklist_line_returns_green_check_for_fully_graded():
    """Fully graded assignments should use a green checkmark line."""
    rendered = WeeklyGradingStatusScreen._format_checklist_line(
        assignment_name="Week 4 Discussion",
        graded_count=12,
        total_submissions=12,
        remaining_count=0,
    )
    assert rendered.startswith("[green]✅ Week 4 Discussion")
    assert "(12/12 graded)" in rendered


def test_format_checklist_line_returns_red_cross_for_partial_grading():
    """Partially graded assignments should show remaining count."""
    rendered = WeeklyGradingStatusScreen._format_checklist_line(
        assignment_name="Week 5 Discussion",
        graded_count=9,
        total_submissions=12,
        remaining_count=3,
    )
    assert rendered.startswith("[red]❌ Week 5 Discussion")
    assert "(9/12 graded — 3 remaining)" in rendered


def test_count_submitted_attempts_counts_gradable_submission_states():
    """Count should include submitted, graded, and pending_review states."""
    screen = WeeklyGradingStatusScreen(_build_app_state())
    screen.app_state.canvas_client.get_assignment_submissions.return_value = [
        SimpleNamespace(workflow_state="submitted"),
        SimpleNamespace(workflow_state="graded"),
        SimpleNamespace(workflow_state="pending_review"),
        SimpleNamespace(workflow_state="unsubmitted"),
    ]

    counted = screen._count_submitted_attempts(course_id=42, assignment_id=101)

    assert counted == 3


def test_count_submitted_attempts_excludes_unsubmitted_states():
    """Count should exclude workflow states outside the gradable set."""
    screen = WeeklyGradingStatusScreen(_build_app_state())
    screen.app_state.canvas_client.get_assignment_submissions.return_value = [
        SimpleNamespace(workflow_state="unsubmitted"),
        SimpleNamespace(workflow_state="deleted"),
        SimpleNamespace(workflow_state="new"),
    ]

    counted = screen._count_submitted_attempts(course_id=42, assignment_id=101)

    assert counted == 0


def test_count_submitted_attempts_returns_zero_on_canvas_error():
    """Submission count should safely return zero when Canvas request fails."""
    screen = WeeklyGradingStatusScreen(_build_app_state())
    screen.app_state.canvas_client.get_assignment_submissions.side_effect = RuntimeError("boom")

    counted = screen._count_submitted_attempts(course_id=42, assignment_id=101)

    assert counted == 0


@pytest.mark.asyncio
async def test_screen_mount_renders_header_and_status_widgets():
    """Screen mount should render key weekly grading status widgets."""
    app_state = _build_app_state()
    app_state.current_course = None
    app = GradingStatusTestApp(app_state)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, WeeklyGradingStatusScreen)
        assert screen.query_one(Header) is not None
        assert screen.query_one("#grading-status-header", Static) is not None
        assert screen.query_one("#grading-status-subtitle", Static) is not None
        assert screen.query_one("#grading-status-checklist", Static) is not None



@pytest.mark.asyncio
async def test_loading_indicator_shows_while_assignments_load_then_hides():
    """The Canvas read runs under a visible LoadingIndicator (HOUSE_STYLE 3)."""
    app_state = _build_app_state()
    fetch_started = threading.Event()
    allow_fetch_finish = threading.Event()

    def slow_get_assignments(course_id):
        fetch_started.set()
        allow_fetch_finish.wait(timeout=2.0)
        return []

    app_state.canvas_client.get_assignments.side_effect = slow_get_assignments
    app = GradingStatusTestApp(app_state)
    async with app.run_test() as pilot:
        await wait_until(pilot, fetch_started.is_set, "assignment fetch started")
        indicator = app.screen.query_one("#grading-status-loading", LoadingIndicator)
        assert indicator.display is True
        allow_fetch_finish.set()
        await wait_until(pilot, lambda: indicator.display is False, "indicator hidden")
