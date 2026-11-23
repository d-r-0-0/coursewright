"""Tests for the Packet Tracer TUI screen.

Uses Textual's ``Pilot`` testing framework to drive the screen
headlessly.  All Canvas API calls are mocked — no real network
traffic occurs and all fixtures use synthetic data.
"""

from __future__ import annotations

from threading import Event
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, LoadingIndicator, Static

from coursewright.core.canvas.models import (
    Assignment,
    Submission,
    SubmissionAttachment,
    SubmissionComment,
)
from coursewright.core.canvas.safe_write import default_backup_directory
from coursewright.ta.packettracer.classifier import (
    COMPLETENESS_COMMENT_MARKER,
    COMPLETENESS_FEEDBACK_MARKER,
    COMPLETENESS_STATUS_MARKER,
)
from coursewright.ui.tui.screens.packet_tracer import PacketTracerScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

from .activity_helpers import wait_until

# ======================================================================
#  Helpers — synthetic Canvas objects and test app host
# ======================================================================


class _FakeCourse:
    """Minimal stand-in for a Canvas Course model."""

    def __init__(self, course_id: int = 42, name: str = "Test Course") -> None:
        self.id = course_id
        self.name = name


def _make_app_state(
    canvas_client=None,
    course_id: int = 42,
) -> AppState:
    """Build an AppState with a synthetic course and optional Canvas mock."""
    state = AppState()
    state.current_course = _FakeCourse(course_id)
    state.canvas_client = canvas_client
    return state


class PacketTracerTestApp(App):
    """Minimal app that pushes a PacketTracerScreen."""

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(PacketTracerScreen(self.app_state))


def _pt_assignment(
    assignment_id: int = 1,
    name: str = "11.2.1 Packet Tracer - NAT",
    extensions: list[str] | None = None,
    points: float = 100.0,
) -> Assignment:
    """Build a synthetic PT-style Assignment for testing."""
    return Assignment(
        id=assignment_id,
        name=name,
        allowed_extensions=extensions or ["pka", "pdf"],
        points_possible=points,
        published=True,
    )


def _non_pt_assignment(
    assignment_id: int = 99,
    name: str = "Reading Response",
) -> Assignment:
    """Build a synthetic non-PT Assignment."""
    return Assignment(
        id=assignment_id,
        name=name,
        published=True,
    )


def _complete_submission(
    user_id: int = 1,
    name: str = "Student One",
    score: float | None = None,
) -> Submission:
    """Synthetic submission with both a document and a PT file."""
    return Submission(
        id=user_id * 100,
        user_id=user_id,
        submitted_at="2025-01-15T12:00:00",
        workflow_state="submitted",
        score=score,
        attachments=[
            SubmissionAttachment(
                id=1,
                display_name="Lab_Report.pdf",
                filename="Lab_Report.pdf",
                size=2048,
            ),
            SubmissionAttachment(
                id=2,
                display_name="network.pka",
                filename="network.pka",
                size=4096,
            ),
        ],
        user={"name": name},
    )


def _incomplete_submission(
    user_id: int = 2,
    name: str = "Student Two",
) -> Submission:
    """Synthetic submission with only a document (missing PT file)."""
    return Submission(
        id=user_id * 100,
        user_id=user_id,
        submitted_at="2025-01-15T12:00:00",
        workflow_state="submitted",
        attachments=[
            SubmissionAttachment(
                id=3,
                display_name="Lab_Report.pdf",
                filename="Lab_Report.pdf",
                size=2048,
            ),
        ],
        user={"name": name},
    )


# ======================================================================
#  Ported source tests (8/8)
# ======================================================================


@pytest.mark.asyncio
async def test_packet_tracer_screen_mounts() -> None:
    """PacketTracerScreen should mount without errors."""
    state = _make_app_state()
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, PacketTracerScreen)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_escape_goes_back() -> None:
    """Pressing Escape should pop the PacketTracerScreen."""
    state = _make_app_state()
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        assert isinstance(app.screen, PacketTracerScreen)
        await pilot.press("escape")
        await wait_until(
            pilot,
            lambda: not isinstance(app.screen, PacketTracerScreen),
            "PacketTracerScreen popped",
        )


@pytest.mark.asyncio
async def test_scan_without_client_shows_error() -> None:
    """Clicking scan without a Canvas client shows an error."""
    state = _make_app_state(canvas_client=None)
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        status = app.screen.query_one("#scan-status", Static)
        assert "No course or Canvas client" in str(status.render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_scan_finds_pt_assignments() -> None:
    """Scanning a course with PT assignments populates the table."""
    mock_client = MagicMock()
    mock_client.get_assignments.return_value = [
        _pt_assignment(assignment_id=1),
        _non_pt_assignment(assignment_id=2),
        _pt_assignment(assignment_id=3, name="5.6.7 Packet Tracer Lab"),
    ]

    state = _make_app_state(canvas_client=mock_client)
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and len(app.screen._detected_assignments) == 2
            ),
            "PT assignments detected",
        )
        screen = app.screen
        assert isinstance(screen, PacketTracerScreen)
        assert len(screen._detected_assignments) == 2
        # Nothing pre-selected — user picks manually
        assert screen._selected_assignment_ids == set()
        await pilot.press("q")


@pytest.mark.asyncio
async def test_scan_no_pt_assignments() -> None:
    """When no PT assignments are detected, all are shown with 0 detected."""
    mock_client = MagicMock()
    mock_client.get_assignments.return_value = [
        _non_pt_assignment(),
    ]

    state = _make_app_state(canvas_client=mock_client)
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and "Loaded 1 assignment"
                in str(app.screen.query_one("#scan-status", Static).render())
            ),
            "scan status updated",
        )
        status = app.screen.query_one("#scan-status", Static)
        rendered = str(status.render())
        assert "0 detected" in rendered
        assert "Loaded 1 assignment" in rendered
        await pilot.press("q")


@pytest.mark.asyncio
async def test_check_without_selection_warns() -> None:
    """Clicking check with no assignments selected shows a warning."""
    state = _make_app_state()
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#check-completeness")
        status = app.screen.query_one("#check-status", Static)
        assert "No assignments selected" in str(status.render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_check_populates_results() -> None:
    """After checking, the results table and reports are populated."""
    mock_client = MagicMock()
    mock_client.get_assignments.return_value = [
        _pt_assignment(assignment_id=1),
    ]
    mock_client.get_assignment_submissions.return_value = [
        _complete_submission(user_id=1, name="Student One"),
        _incomplete_submission(user_id=2, name="Student Two"),
    ]

    state = _make_app_state(canvas_client=mock_client)
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and len(app.screen._detected_assignments) == 1
            ),
            "scan finished",
        )
        await pilot.press("a")
        await pilot.click("#check-completeness")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and 1 in app.screen._reports
            ),
            "completeness check finished",
        )

        screen = app.screen
        assert isinstance(screen, PacketTracerScreen)
        assert 1 in screen._reports
        assert len(screen._reports[1]) == 2
        results_table = screen.query_one("#results-table", DataTable)
        assert results_table.row_count == 2
        summary = str(screen.query_one("#results-summary", Static).render())
        assert "1 of 2 submissions are incomplete" in summary
        await pilot.press("q")


@pytest.mark.asyncio
async def test_post_comments_without_check_warns() -> None:
    """Clicking post comments before checking shows a warning."""
    state = _make_app_state()
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#post-comments")
        status = app.screen.query_one("#comment-status", Static)
        assert "Run completeness check first" in str(status.render())
        await pilot.press("q")


# ======================================================================
#  Confirmation, safe write, and remote-activity tests
# ======================================================================


@pytest.mark.asyncio
async def test_post_comments_cancelled_writes_nothing() -> None:
    """Declining the comment confirmation modal performs no Canvas writes."""
    mock_client = MagicMock()
    mock_client.get_assignments.return_value = [_pt_assignment(assignment_id=1)]
    mock_client.get_assignment_submissions.return_value = [
        _incomplete_submission(user_id=2, name="Student Two"),
    ]

    state = _make_app_state(canvas_client=mock_client)
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and bool(app.screen._detected_assignments)
            ),
            "scan finished",
        )
        await pilot.press("a")
        await pilot.click("#check-completeness")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PacketTracerScreen) and 1 in app.screen._reports,
            "check finished",
        )

        await pilot.press("f")
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PacketTracerScreen),
            "modal dismissed",
        )
        assert "Cancelled" in str(app.screen.query_one("#comment-status", Static).render())
        mock_client.post_submission_comment.assert_not_called()
        mock_client.delete_submission_comment.assert_not_called()
        await pilot.press("q")


@pytest.mark.asyncio
async def test_post_comments_confirmed_replaces_old_and_posts_new(
    monkeypatch,
    tmp_path,
) -> None:
    """Confirming comment post cleans up old markers and posts split comments."""
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))

    mock_client = MagicMock()
    mock_client.get_assignments.return_value = [_pt_assignment(assignment_id=1)]
    mock_client.get_assignment_submissions.return_value = [
        _incomplete_submission(user_id=2, name="Student Two"),
    ]
    mock_client.get_submissions_with_comments.return_value = [
        Submission(
            id=200,
            user_id=2,
            submission_comments=[
                SubmissionComment(id=10, comment=f"{COMPLETENESS_STATUS_MARKER} old"),
                SubmissionComment(id=11, comment=f"{COMPLETENESS_FEEDBACK_MARKER} old"),
                SubmissionComment(id=12, comment=f"{COMPLETENESS_COMMENT_MARKER} legacy"),
            ],
        )
    ]
    mock_client.get_submission.return_value = {"id": 200, "score": None}
    mock_client.delete_submission_comment.return_value = True
    mock_client.post_submission_comment.return_value = True

    state = _make_app_state(canvas_client=mock_client)
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and bool(app.screen._detected_assignments)
            ),
            "scan finished",
        )
        await pilot.press("a")
        await pilot.click("#check-completeness")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PacketTracerScreen) and 1 in app.screen._reports,
            "check finished",
        )

        await pilot.press("f")
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("y")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and "Posted 2" in str(app.screen.query_one("#comment-status", Static).render())
            ),
            "comments posted",
        )

        deleted_ids = [
            call.args[3] for call in mock_client.delete_submission_comment.call_args_list
        ]
        assert sorted(deleted_ids) == [10, 11, 12]
        assert mock_client.post_submission_comment.call_count == 2
        status_text = str(app.screen.query_one("#comment-status", Static).render())
        assert "replaced 1 old" in status_text
        assert "fallback" in status_text.lower()
        backups = list(
            default_backup_directory(tmp_path).glob("*_post_submission_comment.json")
        )
        assert len(backups) == 2
        await pilot.press("q")


@pytest.mark.asyncio
async def test_auto_grade_confirmed_uses_safe_writer_and_skips_graded(
    monkeypatch,
    tmp_path,
) -> None:
    """Auto-grade writes backups via SafeCanvasWriter and skips already-graded users."""
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))

    mock_client = MagicMock()
    mock_client.get_assignments.return_value = [_pt_assignment(assignment_id=1, points=50.0)]
    mock_client.get_assignment_submissions.return_value = [
        _complete_submission(user_id=1, name="Student One", score=None),
        _complete_submission(user_id=3, name="Student Three", score=50.0),
    ]
    mock_client.get_submission.return_value = {"id": 100, "score": None}
    mock_client.submit_grade.return_value = True

    state = _make_app_state(canvas_client=mock_client)
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and bool(app.screen._detected_assignments)
            ),
            "scan finished",
        )
        await pilot.press("a")
        await pilot.click("#check-completeness")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PacketTracerScreen) and 1 in app.screen._reports,
            "check finished",
        )

        await pilot.press("g")
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("y")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and "Graded 1 at 100%"
                in str(app.screen.query_one("#grade-status", Static).render())
            ),
            "auto-grade finished",
        )

        status_text = str(app.screen.query_one("#grade-status", Static).render())
        assert "skipped 1 (already graded)" in status_text
        mock_client.submit_grade.assert_called_once_with(
            42,
            1,
            1,
            50.0,
            "Pending Review 11.2.1 Packet Tracer - NAT 100%",
        )
        backups = list(default_backup_directory(tmp_path).glob("*_submit_grade.json"))
        assert len(backups) == 1
        await pilot.press("q")


@pytest.mark.asyncio
async def test_scan_shows_transmission_modal_while_running() -> None:
    """Remote scan displays TransmissionModal until Canvas responds."""
    entered = Event()
    release = Event()

    class _HeldScanClient:
        def get_assignments(self, course_id: int) -> list[Assignment]:
            entered.set()
            release.wait(timeout=5)
            return [_pt_assignment(assignment_id=1)]

    state = _make_app_state(canvas_client=_HeldScanClient())
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        await wait_until(pilot, entered.is_set, "scan entered client")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, TransmissionModal),
            "transmission modal shown",
        )
        assert app.screen.query_one("#transmission-spinner", LoadingIndicator) is not None
        release.set()
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and len(app.screen._detected_assignments) == 1
            ),
            "scan completed and modal dismissed",
        )
        await pilot.press("q")


@pytest.mark.asyncio
async def test_auto_grade_without_workspace_reports_error_without_crashing(
    monkeypatch,
) -> None:
    """Confirming auto-grade without COURSEWRIGHT_WORKSPACE surfaces an error safely."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.setattr(
        "coursewright.core.canvas.safe_write.resolve_workspace_directory",
        lambda: None,
    )

    mock_client = MagicMock()
    mock_client.get_assignments.return_value = [_pt_assignment(assignment_id=1, points=50.0)]
    mock_client.get_assignment_submissions.return_value = [
        _complete_submission(user_id=1, name="Student One", score=None),
    ]

    state = _make_app_state(canvas_client=mock_client)
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and bool(app.screen._detected_assignments)
            ),
            "scan finished",
        )
        await pilot.press("a")
        await pilot.click("#check-completeness")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PacketTracerScreen) and 1 in app.screen._reports,
            "check finished",
        )

        pt_screen = app.screen
        await pilot.press("g")
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("y")

        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, TransmissionModal)
                and app.screen._has_error
            ),
            "transmission modal entered error state",
        )
        assert app.is_running
        await pilot.press("escape")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PacketTracerScreen),
            "returned to PacketTracerScreen after dismissing error modal",
        )

        status_text = str(pt_screen.query_one("#grade-status", Static).render())
        assert "Auto-grade failed" in status_text
        assert "COURSEWRIGHT_WORKSPACE" in status_text
        mock_client.submit_grade.assert_not_called()
        await pilot.press("q")


@pytest.mark.asyncio
async def test_post_comments_without_workspace_reports_error_without_crashing(
    monkeypatch,
) -> None:
    """Confirming comment post without COURSEWRIGHT_WORKSPACE surfaces an error safely."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.setattr(
        "coursewright.core.canvas.safe_write.resolve_workspace_directory",
        lambda: None,
    )

    mock_client = MagicMock()
    mock_client.get_assignments.return_value = [_pt_assignment(assignment_id=1)]
    mock_client.get_assignment_submissions.return_value = [
        _incomplete_submission(user_id=2, name="Student Two"),
    ]

    state = _make_app_state(canvas_client=mock_client)
    app = PacketTracerTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#scan-course")
        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, PacketTracerScreen)
                and bool(app.screen._detected_assignments)
            ),
            "scan finished",
        )
        await pilot.press("a")
        await pilot.click("#check-completeness")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PacketTracerScreen) and 1 in app.screen._reports,
            "check finished",
        )

        pt_screen = app.screen
        await pilot.press("f")
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("y")

        await wait_until(
            pilot,
            lambda: (
                isinstance(app.screen, TransmissionModal)
                and app.screen._has_error
            ),
            "transmission modal entered error state",
        )
        assert app.is_running
        await pilot.press("escape")
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, PacketTracerScreen),
            "returned to PacketTracerScreen after dismissing error modal",
        )

        status_text = str(pt_screen.query_one("#comment-status", Static).render())
        assert "Post comments failed" in status_text
        assert "COURSEWRIGHT_WORKSPACE" in status_text
        mock_client.post_submission_comment.assert_not_called()
        mock_client.delete_submission_comment.assert_not_called()
        await pilot.press("q")
