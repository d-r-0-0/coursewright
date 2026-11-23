"""Remote activity is always visible in the Textual app.

docs/HOUSE_STYLE.md section 3 and the card's acceptance item 4: any
screen that triggers a remote call shows the transmission log or a
loading indicator while the call runs.  Each test here holds a fake
Canvas call open on a ``threading.Event`` so the "while it runs" state
can be inspected with the Textual pilot, then releases it.

Also covers the application shell: its name and the ``tui`` command.
"""

from __future__ import annotations

from threading import Event

import pytest
from textual.screen import Screen
from textual.widgets import DataTable, LoadingIndicator, Static

from coursewright.core import traffic
from coursewright.ui.tui.screens.course_list import CourseListScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

from .activity_helpers import (
    HeldCanvasClient,
    OneScreenApp,
    find_screen,
    make_course_dict,
    wait_until,
)


@pytest.mark.asyncio
async def test_initial_course_fetch_runs_under_the_transmission_log() -> None:
    release = Event()
    client = HeldCanvasClient(release, courses=[make_course_dict()])
    state = AppState()
    state.canvas_client = client
    app = OneScreenApp(CourseListScreen(state), state)
    traffic.reset_for_tests()
    async with app.run_test() as pilot:
        await wait_until(
            pilot, lambda: isinstance(app.screen, TransmissionModal), "transmission modal"
        )
        modal = app.screen
        assert modal.query_one("#transmission-spinner", LoadingIndicator) is not None
        assert "Loading courses" in str(modal.query_one("#transmission-status").render())

        # A published traffic event lands in the log while the call is held open.
        traffic.publish(traffic.TrafficEvent("canvas", "GET", "courses", 200, 12))
        await wait_until(pilot, lambda: bool(modal._log_lines), "log line")
        assert "courses" in modal._log_lines[0]

        release.set()
        await wait_until(
            pilot, lambda: isinstance(app.screen, CourseListScreen), "modal closed"
        )
        table = app.screen.query_one("#course-table", DataTable)
        await wait_until(pilot, lambda: table.row_count == 1, "course row loaded")
        # The grading to-do fetch that follows opens its own modal; it must
        # close too, leaving the course list on top.
        await wait_until(
            pilot, lambda: isinstance(app.screen, CourseListScreen), "all modals closed"
        )
    traffic.reset_for_tests()


@pytest.mark.asyncio
async def test_failed_course_fetch_keeps_the_transmission_log_open() -> None:
    class BrokenClient(HeldCanvasClient):
        def get_courses(self) -> list[dict]:
            raise RuntimeError("canvas unreachable")

    state = AppState()
    state.canvas_client = BrokenClient(Event())
    app = OneScreenApp(CourseListScreen(state), state)
    async with app.run_test() as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, TransmissionModal) and app.screen._has_error,
            "error state",
        )
        assert "canvas unreachable" in str(app.screen.query_one("#transmission-status").render())
        await pilot.press("escape")
        await wait_until(pilot, lambda: isinstance(app.screen, CourseListScreen), "dismissed")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["participation", "average grade"])
async def test_background_refresh_shows_a_loading_indicator_while_it_runs(operation) -> None:
    release = Event()
    client = HeldCanvasClient(release)
    if operation == "average grade":
        client.get_course_average_grades = client.get_last_student_participation_by_course
        client.get_last_student_participation_by_course = lambda course_ids: {}
    state = AppState()
    state.canvas_client = client
    state.courses = state.filtered_courses = [_course_model(7)]
    app = OneScreenApp(CourseListScreen(state), state)
    async with app.run_test() as pilot:
        await wait_until(pilot, client.entered.is_set, "participation fetch started")
        screen = find_screen(app, CourseListScreen)
        await wait_until(
            pilot,
            lambda: screen.query_one("#background-activity").display is True,
            "indicator shown",
        )
        status_text = str(screen.query_one("#background-status", Static).render())
        assert operation in status_text
        assert screen.query_one("#background-loading", LoadingIndicator) is not None

        release.set()
        await wait_until(
            pilot,
            lambda: screen.query_one("#background-activity").display is False,
            "indicator hidden",
        )


def _course_model(course_id: int):
    from coursewright.core.canvas.models import CanvasCourse

    return CanvasCourse.model_validate(make_course_dict(course_id))


# ----------------------------------------------------------------------
# Attendance: a remote report behind a shortcut
# ----------------------------------------------------------------------


class RecordingAttendanceScreen(Screen):
    """Stand-in for AttendanceScreen, so only the loading step is tested."""

    def __init__(self, app_state: AppState, report: object) -> None:
        super().__init__()
        self.app_state = app_state
        self.report = report


def prepare_attendance_app(monkeypatch, fake_check_attendance) -> tuple[OneScreenApp, AppState]:
    monkeypatch.setattr(
        "coursewright.ui.tui.screens.course_list.find_tool_screen_class",
        lambda tool_name: RecordingAttendanceScreen,
    )
    monkeypatch.setattr(
        "coursewright.ta.attendance.status.check_attendance", fake_check_attendance
    )
    class QuickClient(HeldCanvasClient):
        pass

    state = AppState()
    state.canvas_client = QuickClient(Event())
    state.courses = state.filtered_courses = [_course_model(7)]
    return OneScreenApp(CourseListScreen(state), state), state


@pytest.mark.asyncio
async def test_attendance_report_runs_under_the_transmission_log(monkeypatch) -> None:
    release = Event()
    started = Event()

    def held_check_attendance(canvas_client, course_id, progress_callback=None, **_kwargs):
        started.set()
        progress_callback("Loading enrolled students…")
        release.wait(timeout=5)
        return {"course_id": course_id}

    app, _state = prepare_attendance_app(monkeypatch, held_check_attendance)
    async with app.run_test() as pilot:
        await wait_until(
            pilot, lambda: isinstance(app.screen, CourseListScreen), "course list ready"
        )
        await pilot.press("a")
        await wait_until(pilot, started.is_set, "attendance fetch started")
        await wait_until(
            pilot, lambda: isinstance(app.screen, TransmissionModal), "transmission modal"
        )
        modal = app.screen
        status_line = modal.query_one("#transmission-status", Static)
        await wait_until(
            pilot,
            lambda: "Loading enrolled students" in str(status_line.render()),
            "progress relayed to the status line",
        )

        release.set()
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, RecordingAttendanceScreen),
            "attendance screen pushed",
        )
        assert app.screen.report == {"course_id": 7}


@pytest.mark.asyncio
async def test_attendance_failure_leaves_the_error_in_the_log(monkeypatch) -> None:
    def failing_check_attendance(*_args, **_kwargs):
        raise RuntimeError("attendance exploded")

    app, _state = prepare_attendance_app(monkeypatch, failing_check_attendance)
    async with app.run_test() as pilot:
        await wait_until(
            pilot, lambda: isinstance(app.screen, CourseListScreen), "course list ready"
        )
        await pilot.press("a")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, TransmissionModal) and app.screen._has_error,
            "error state",
        )
        assert "attendance exploded" in str(app.screen.query_one("#transmission-status").render())
