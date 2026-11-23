"""Tests for AttendanceScreen and WarningPreviewScreen.

The screen half of discussorator's ``tests/test_attendance.py`` (the
library half lives in ``tests/ta/attendance/test_attendance.py``).  The
template-helper tests moved with the helpers to ``ta``; the rest are
ported here with synthetic course codes.

New tests prove that a confirmed warning really is sent (the library
defaults to a dry run), that it is backed up first, that the send runs
under the transmission log, and that with no workspace nothing is sent
and the user is told (AGENTS.md 3.11, docs/HOUSE_STYLE.md section 3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static

from coursewright.ta.attendance.status import AttendanceReport, AttendanceStudentRecord
from coursewright.ui.tui.screens.attendance import AttendanceScreen, WarningPreviewScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal
from tests.ui.tui.activity_helpers import wait_until


class AttendanceTestApp(App):
    """Minimal Textual app that pushes AttendanceScreen on mount."""

    def __init__(self, app_state: AppState, report: AttendanceReport) -> None:
        super().__init__()
        self._app_state = app_state
        self._report = report

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(AttendanceScreen(self._app_state, self._report))


def _make_report() -> AttendanceReport:
    """Create an attendance report fixture with green/yellow/red records."""
    now_utc = datetime.now(UTC)
    return AttendanceReport(
        lookback_days=14,
        generated_at=now_utc,
        student_records=[
            AttendanceStudentRecord(
                student_id=1,
                student_name="Alice Student",
                last_activity_at=now_utc - timedelta(days=1),
                last_submission_at=now_utc - timedelta(days=2),
                last_discussion_post_at=None,
                attendance_status="green",
            ),
            AttendanceStudentRecord(
                student_id=2,
                student_name="Bob Learner",
                last_activity_at=now_utc - timedelta(days=10),
                last_submission_at=now_utc - timedelta(days=10),
                last_discussion_post_at=None,
                attendance_status="yellow",
            ),
            AttendanceStudentRecord(
                student_id=3,
                student_name="Charlie Newbie",
                last_activity_at=None,
                last_submission_at=None,
                last_discussion_post_at=None,
                attendance_status="red",
            ),
        ],
    )


class TestAttendanceScreenHelpers:
    """Unit tests for AttendanceScreen static helper methods."""

    def test_truncate_student_name_short(self) -> None:
        short_name = "Short Name"
        assert AttendanceScreen._truncate_student_name(short_name) == short_name

    def test_truncate_student_name_long(self) -> None:
        long_name = "A" * 40
        assert AttendanceScreen._truncate_student_name(long_name) == ("A" * 25 + "...")

    @pytest.mark.parametrize(
        ("status_value", "expected_markup"),
        [
            ("green", "[green]● Green[/green]"),
            ("yellow", "[yellow]● Yellow[/yellow]"),
            ("red", "[red]● Red[/red]"),
        ],
    )
    def test_format_status(self, status_value: str, expected_markup: str) -> None:
        assert AttendanceScreen._format_status(status_value) == expected_markup

    def test_format_datetime_none_uses_em_dash(self) -> None:
        assert AttendanceScreen._format_datetime(None) == "—"

    def test_format_datetime_formats_timestamp(self) -> None:
        value = datetime(2026, 3, 1, 15, 30, tzinfo=UTC)
        assert AttendanceScreen._format_datetime(value) == "03/01/2026"


@pytest.mark.asyncio
async def test_attendance_screen_mounts_with_data_table() -> None:
    """AttendanceScreen should mount and populate the attendance DataTable."""
    app = AttendanceTestApp(AppState(), _make_report())
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#attendance-table", DataTable)
        assert table is not None
        assert table.row_count == 3


@pytest.mark.asyncio
async def test_attendance_screen_summary_shows_status_counts() -> None:
    """Summary panel should show one green, one yellow, and one red."""
    app = AttendanceTestApp(AppState(), _make_report())
    async with app.run_test() as pilot:
        await pilot.pause()
        summary_widget = app.screen.query_one("#attendance-summary", Static)
        summary_text = str(summary_widget.render())
        assert "Green" in summary_text and "1" in summary_text
        assert "Yellow" in summary_text and "1" in summary_text
        assert "Red" in summary_text and "1" in summary_text


# =================================================================
# WarningPreviewScreen tests
# =================================================================


class WarningPreviewTestApp(App):
    """Minimal Textual app that pushes WarningPreviewScreen on mount."""

    def __init__(
        self,
        app_state: AppState,
        student_record: AttendanceStudentRecord,
        subject_line: str,
        message_body: str,
    ) -> None:
        super().__init__()
        self._app_state = app_state
        self._student_record = student_record
        self._subject_line = subject_line
        self._message_body = message_body

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(
            WarningPreviewScreen(
                app_state=self._app_state,
                student_record=self._student_record,
                subject_line=self._subject_line,
                message_body=self._message_body,
            )
        )


def _flagged_record(name: str = "Warning Student", status: str = "red"):
    now_utc = datetime.now(UTC)
    return AttendanceStudentRecord(
        student_id=99,
        student_name=name,
        last_activity_at=now_utc - timedelta(days=20),
        last_submission_at=now_utc - timedelta(days=20),
        last_discussion_post_at=None,
        attendance_status=status,
    )


@pytest.mark.asyncio
async def test_warning_preview_screen_shows_student_name() -> None:
    """WarningPreviewScreen should display the student name in its header."""
    app = WarningPreviewTestApp(
        app_state=AppState(),
        student_record=_flagged_record(),
        subject_line="Attendance Warning for COURSE101",
        message_body="Dear Warning Student, you need to check in.",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        header_widget = app.screen.query_one("#warning-preview-header", Static)
        header_text = str(header_widget.render())
        assert "Warning Student" in header_text


@pytest.mark.asyncio
async def test_warning_preview_screen_shows_subject_and_body() -> None:
    """The preview should render the subject line and message body."""
    app = WarningPreviewTestApp(
        app_state=AppState(),
        student_record=_flagged_record("Preview Student", "yellow"),
        subject_line="Warning: COURSE101",
        message_body="This is the message body text.",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        subject_widget = app.screen.query_one("#warning-preview-subject", Static)
        body_widget = app.screen.query_one("#warning-preview-body", Static)
        assert "COURSE101" in str(subject_widget.render())
        assert "message body text" in str(body_widget.render())


# =================================================================
# AttendanceScreen row-click tests
# =================================================================


@pytest.mark.asyncio
async def test_attendance_screen_row_click_green_shows_notification() -> None:
    """Clicking a green student row should show 'no warning needed'."""
    report = _make_report()
    app = AttendanceTestApp(AppState(), report)
    async with app.run_test(notifications=True) as pilot:
        await pilot.pause()
        # Alice is green and appears first alphabetically (row index 0)
        table = app.screen.query_one("#attendance-table", DataTable)
        table.move_cursor(row=0)
        await pilot.press("enter")
        await pilot.pause()
        # Should stay on AttendanceScreen (not push preview)
        assert isinstance(app.screen, AttendanceScreen)


@pytest.mark.asyncio
async def test_attendance_screen_stores_row_key_mapping() -> None:
    """The screen should build a row-key-to-student-record mapping."""
    report = _make_report()
    app = AttendanceTestApp(AppState(), report)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, AttendanceScreen)
        assert len(screen._student_record_by_row_key) == 3


@pytest.mark.asyncio
async def test_red_row_opens_a_filled_warning_preview() -> None:
    """A flagged row opens the preview with the template filled in."""
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="COURSE 101")
    app = AttendanceTestApp(state, _make_report())
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#attendance-table", DataTable)
        table.move_cursor(row=2)  # Charlie Newbie, red
        await pilot.press("enter")
        await wait_until(
            pilot, lambda: isinstance(app.screen, WarningPreviewScreen), "warning preview"
        )
        body = str(app.screen.query_one("#warning-preview-body", Static).render())
        assert "Charlie" in body
        assert "{" not in body  # every placeholder was filled


# =================================================================
# Sending: confirmed sends execute, back up first and show activity
# =================================================================


def _state_with_client(client) -> AppState:
    state = AppState()
    state.canvas_client = client
    state.current_course = SimpleNamespace(id=42, name="COURSE 101")
    return state


@pytest.mark.asyncio
async def test_confirmed_single_warning_is_backed_up_then_sent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    client = MagicMock()
    client.post_conversation_message.return_value = {"id": 7}
    app = WarningPreviewTestApp(
        app_state=_state_with_client(client),
        student_record=_flagged_record(),
        subject_line="Checking in",
        message_body="Synthetic body.",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await wait_until(pilot, lambda: isinstance(app.screen, ConfirmModal), "confirm")
        client.post_conversation_message.assert_not_called()
        await pilot.press("y")
        await wait_until(
            pilot,
            lambda: not any(isinstance(s, WarningPreviewScreen) for s in app.screen_stack),
            "preview closed after sending",
        )
    client.post_conversation_message.assert_called_once_with(
        42, 99, "Checking in", "Synthetic body."
    )
    assert len(list(Path(tmp_path).rglob("*.json"))) == 1


@pytest.mark.asyncio
async def test_single_warning_without_a_workspace_is_not_sent(monkeypatch) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    client = MagicMock()
    app = WarningPreviewTestApp(
        app_state=_state_with_client(client),
        student_record=_flagged_record(),
        subject_line="Checking in",
        message_body="Synthetic body.",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await wait_until(pilot, lambda: isinstance(app.screen, ConfirmModal), "confirm")
        await pilot.press("y")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, TransmissionModal) and app.screen._has_error,
            "transmission log shows why nothing was sent",
        )
        status = str(app.screen.query_one("#transmission-status", Static).render())
        assert "COURSEWRIGHT_WORKSPACE" in status
    client.post_conversation_message.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_send_warns_every_flagged_student_once(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    client = MagicMock()
    client.post_conversation_message.return_value = {"id": 7}
    app = AttendanceTestApp(_state_with_client(client), _make_report())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await wait_until(pilot, lambda: isinstance(app.screen, ConfirmModal), "confirm")
        await pilot.press("y")
        await wait_until(
            pilot,
            lambda: (
                client.post_conversation_message.call_count == 2
                and isinstance(app.screen, AttendanceScreen)
            ),
            "both flagged students warned and the log closed",
        )
        screen = app.screen
        assert screen._already_warned_student_ids == {2, 3}
    recipients = sorted(call.args[1] for call in client.post_conversation_message.call_args_list)
    assert recipients == [2, 3]  # never the green student
    assert len(list(Path(tmp_path).rglob("*.json"))) == 2
