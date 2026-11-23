"""Every Tools shortcut on the course list opens its real screen.

``test_course_list_screen.py`` swaps the tool screens for recording
stand-ins so it can test routing on its own.  These tests do the
opposite: they start from a real ``CourseListScreen`` and check that each
shortcut pushes the concrete, ported screen, not a stand-in and not the
"not available in this build yet" notice.

Canvas is a ``MagicMock`` that returns empty lists, and ``HOME`` points
at an empty folder so screens that look in Downloads for an export find
nothing real.  Every name and id is synthetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from coursewright.core.canvas.models import CanvasCourse
from coursewright.ta.attendance.status import AttendanceReport, AttendanceStudentRecord
from coursewright.ui.tui.screens.attendance import AttendanceScreen
from coursewright.ui.tui.screens.bulk_comment import BulkCommentScreen
from coursewright.ui.tui.screens.course_list import CourseListScreen
from coursewright.ui.tui.screens.discussion_list import DiscussionListScreen
from coursewright.ui.tui.screens.gradeliner import GradeLinerScreen
from coursewright.ui.tui.screens.packet_tracer import PacketTracerScreen
from coursewright.ui.tui.screens.zero_ungraded import ZeroUngradedScreen
from coursewright.ui.tui.screens.zybooks import ZyBooksScreen
from coursewright.ui.tui.state import AppState
from tests.ui.tui.activity_helpers import OneScreenApp, find_screen, wait_until


def make_course() -> CanvasCourse:
    return CanvasCourse(
        id=42,
        name="Synthetic Course",
        course_code="COURSE101",
        term={"name": "Fall 2026"},
        total_students=1,
        workflow_state="available",
        enrollments=[],
    )


def make_canvas_client() -> MagicMock:
    """A Canvas fake whose every list read comes back empty."""
    client = MagicMock()
    client.get_assignments.return_value = []
    client.get_active_submissions_for_assignments.return_value = []
    client.get_discussions.return_value = []
    client.get_assignment_submissions.return_value = []
    return client


def make_state() -> AppState:
    state = AppState()
    state.canvas_client = make_canvas_client()
    state.filtered_courses = [make_course()]
    return state


@pytest.fixture(autouse=True)
def _empty_home(monkeypatch, tmp_path):
    """Keep Downloads-first file detection away from the real home folder."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


async def press_on_course_list(app: OneScreenApp, pilot, key: str) -> None:
    await wait_until(
        pilot, lambda: isinstance(app.screen, CourseListScreen), "course list ready"
    )
    await pilot.press(key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "screen_class"),
    [
        ("d", DiscussionListScreen),
        ("b", BulkCommentScreen),
        ("z", ZeroUngradedScreen),
        ("g", GradeLinerScreen),
        ("y", ZyBooksScreen),
        ("p", PacketTracerScreen),
    ],
)
async def test_tools_shortcut_pushes_its_concrete_screen(key, screen_class):
    state = make_state()
    app = OneScreenApp(CourseListScreen(state), state)
    async with app.run_test() as pilot:
        await press_on_course_list(app, pilot, key)
        await wait_until(
            pilot,
            lambda: find_screen(app, screen_class) is not None,
            f"{screen_class.__name__} pushed by {key!r}",
        )
        pushed = find_screen(app, screen_class)
        assert pushed.app_state.current_course.id == 42
        notices = [str(note.message) for note in app._notifications]
        assert not any("not available in this build" in notice for notice in notices)


@pytest.mark.asyncio
async def test_attendance_shortcut_loads_the_report_then_pushes_attendance_screen(
    monkeypatch,
):
    """``a`` runs the attendance check, then opens AttendanceScreen with it."""
    report = AttendanceReport(
        lookback_days=14,
        generated_at=datetime.now(UTC),
        student_records=[
            AttendanceStudentRecord(
                student_id=1,
                student_name="Student One",
                last_activity_at=None,
                last_submission_at=None,
                last_discussion_post_at=None,
                attendance_status="red",
            )
        ],
    )
    requested_courses: list[int] = []

    def fake_check_attendance(canvas_client, course_id, progress_callback=None, **_kwargs):
        requested_courses.append(course_id)
        return report

    monkeypatch.setattr(
        "coursewright.ta.attendance.status.check_attendance", fake_check_attendance
    )
    state = make_state()
    app = OneScreenApp(CourseListScreen(state), state)
    async with app.run_test() as pilot:
        await press_on_course_list(app, pilot, "a")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, AttendanceScreen),
            "AttendanceScreen pushed by 'a'",
        )
        assert app.screen.attendance_report is report
    assert requested_courses == [42]
