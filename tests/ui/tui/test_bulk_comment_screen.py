"""Tests for the TUI-10 BulkCommentScreen.

Uses Textual's ``Pilot`` testing framework to drive the screen
headlessly.  All Canvas API calls are mocked so no real network
traffic occurs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label, Static

from coursewright.core.canvas.models import Submission
from coursewright.ui.tui.screens.bulk_comment import (
    BUILTIN_TEMPLATE,
    BulkCommentScreen,
    _build_submission_summaries,
    _load_comment_template_text,
)
from coursewright.ui.tui.state import AppState
from tests.ui.tui.activity_helpers import wait_until

# ======================================================================
# Helpers — fake Canvas objects and test app host
# ======================================================================


class _FakeAssignment:
    """Minimal stand-in for a Canvas Assignment model."""

    def __init__(
        self,
        assignment_id: int,
        name: str,
        points: float,
        published: bool = True,
    ) -> None:
        self.id = assignment_id
        self.name = name
        self.points_possible = points
        self.published = published


class _FakeCourse:
    """Minimal stand-in for a Canvas Course model."""

    def __init__(self, course_id: int = 42) -> None:
        self.id = course_id


def _make_app_state(
    canvas_client=None,
    course_id: int = 42,
) -> AppState:
    """Build an AppState with a fake course and optional Canvas mock."""
    state = AppState()
    state.current_course = _FakeCourse(course_id)
    state.canvas_client = canvas_client
    return state


class BulkCommentTestApp(App):
    """Minimal app that pushes a BulkCommentScreen."""

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self._app_state = app_state

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(BulkCommentScreen(self._app_state))


# ======================================================================
# Unit tests — module-level helpers
# ======================================================================


class TestBuildSubmissionSummaries:
    """Tests for _build_submission_summaries helper."""

    def test_identifies_missing_students(self):
        """Students with no submitted_at and no grade are flagged as missing."""
        mock_client = MagicMock()
        mock_client.get_assignment_submissions.return_value = [
            Submission(
                id=101,
                user_id=1,
                submitted_at="2025-01-01T00:00:00Z",
                grade="A",
                user={"name": "Alice"},
            ),
            Submission(
                id=102,
                user_id=2,
                submitted_at=None,
                grade=None,
                user={"name": "Bob"},
            ),
        ]
        assignments = [_FakeAssignment(10, "Essay 1", 100.0)]
        result = _build_submission_summaries(mock_client, 42, assignments)

        assert len(result) == 1
        assert result[0]["missing_count"] == 1
        assert result[0]["missing_students"][0]["name"] == "Bob"
        assert result[0]["total_submissions"] == 2

    def test_skips_null_user_id(self):
        """Submissions with user_id=0 (Test Student) are skipped."""
        mock_client = MagicMock()
        mock_client.get_assignment_submissions.return_value = [
            Submission(id=201, user_id=0, submitted_at=None, grade=None),
            Submission(
                id=202,
                user_id=5,
                submitted_at=None,
                grade=None,
                user={"name": "Charlie"},
            ),
        ]
        assignments = [_FakeAssignment(20, "Quiz 1", 50.0)]
        result = _build_submission_summaries(mock_client, 42, assignments)

        assert result[0]["total_submissions"] == 1
        assert result[0]["missing_count"] == 1

    def test_handles_api_failure_gracefully(self):
        """If fetching submissions fails, that assignment is skipped."""
        mock_client = MagicMock()
        mock_client.get_assignment_submissions.side_effect = Exception("API down")
        assignments = [_FakeAssignment(30, "Broken", 100.0)]
        result = _build_submission_summaries(mock_client, 42, assignments)

        assert result == []

    def test_graded_but_not_submitted_is_not_missing(self):
        """A student with a grade but no submission is not flagged."""
        mock_client = MagicMock()
        mock_client.get_assignment_submissions.return_value = [
            Submission(
                id=401,
                user_id=3,
                submitted_at=None,
                grade="B+",
                user={"name": "Dana"},
            ),
        ]
        assignments = [_FakeAssignment(40, "Lab 1", 25.0)]
        result = _build_submission_summaries(mock_client, 42, assignments)

        assert result[0]["missing_count"] == 0

    def test_fallback_name_when_user_key_missing(self):
        """If 'user' dict is missing, fall back to 'User <id>' format."""
        mock_client = MagicMock()
        mock_client.get_assignment_submissions.return_value = [
            Submission(id=501, user_id=99, submitted_at=None, grade=None),
        ]
        assignments = [_FakeAssignment(50, "Homework 1", 10.0)]
        result = _build_submission_summaries(mock_client, 42, assignments)

        assert result[0]["missing_students"][0]["name"] == "User 99"


class TestLoadCommentTemplateText:
    """Tests for _load_comment_template_text helper."""

    def test_returns_builtin_when_file_missing(self, tmp_path, monkeypatch):
        """When no template file exists, the built-in fallback is used."""
        fake_path = tmp_path / "templates" / "missing_submission.md"
        monkeypatch.setattr(
            "coursewright.ui.tui.screens.bulk_comment.DEFAULT_TEMPLATE_PATH",
            fake_path,
        )
        result = _load_comment_template_text()
        assert result == BUILTIN_TEMPLATE
        # The function should also write the file for future edits
        assert fake_path.exists()

    def test_reads_existing_template(self, tmp_path, monkeypatch):
        """When a template file exists, its content is returned."""
        fake_path = tmp_path / "templates" / "missing_submission.md"
        fake_path.parent.mkdir(parents=True)
        fake_path.write_text("Custom template for {student_name}.\n")
        monkeypatch.setattr(
            "coursewright.ui.tui.screens.bulk_comment.DEFAULT_TEMPLATE_PATH",
            fake_path,
        )
        result = _load_comment_template_text()
        assert result == "Custom template for {student_name}."


# ======================================================================
# Textual Pilot integration tests — screen lifecycle
# ======================================================================


@pytest.mark.asyncio
async def test_bulk_comment_screen_mounts():
    """BulkCommentScreen should mount without errors."""
    state = _make_app_state()
    app = BulkCommentTestApp(state)
    async with app.run_test() as pilot:
        # Verify the screen is the active one
        screen = app.screen
        assert isinstance(screen, BulkCommentScreen)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_bulk_comment_screen_escape_goes_back():
    """Pressing Escape should pop the BulkCommentScreen."""
    state = _make_app_state()
    app = BulkCommentTestApp(state)
    async with app.run_test() as pilot:
        assert isinstance(app.screen, BulkCommentScreen)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_fetch_with_no_client_shows_error():
    """Clicking fetch without a Canvas client shows an error."""
    state = _make_app_state(canvas_client=None)
    app = BulkCommentTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#fetch-assignments")
        status = app.screen.query_one("#load-status", Static)
        assert "No course or Canvas client" in str(status.render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_fetch_populates_selection_table():
    """After a successful fetch the SelectionTable should have rows."""
    mock_client = MagicMock()
    mock_client.get_assignments.return_value = [
        _FakeAssignment(1, "Essay 1", 100.0),
    ]
    mock_client.get_assignment_submissions.return_value = [
        Submission(
            id=1001,
            user_id=10,
            submitted_at=None,
            grade=None,
            user={"name": "Alice"},
        ),
    ]

    state = _make_app_state(canvas_client=mock_client)
    app = BulkCommentTestApp(state)
    async with app.run_test() as pilot:
        screen = app.screen
        await pilot.click("#fetch-assignments")
        # The fetch worker pushes a TransmissionModal on top.  Wait for
        # it to pop (worker complete) before asserting on the underlying
        # BulkCommentScreen state.
        max_wait_iterations = 40
        for _ in range(max_wait_iterations):
            if app.screen is screen:
                break
            await pilot.pause(0.05)
        assert isinstance(screen, BulkCommentScreen)
        assert len(screen._assignment_summaries) == 1
        assert screen._selected_assignment_ids == {1}
        await pilot.press("q")


@pytest.mark.asyncio
async def test_load_template_shows_preview(tmp_path, monkeypatch):
    """Loading the template should display preview text."""
    fake_path = tmp_path / "templates" / "missing_submission.md"
    fake_path.parent.mkdir(parents=True)
    fake_path.write_text("Hello {student_name}, submit {assignment_name}.\n")
    monkeypatch.setattr(
        "coursewright.ui.tui.screens.bulk_comment.DEFAULT_TEMPLATE_PATH",
        fake_path,
    )

    state = _make_app_state()
    app = BulkCommentTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#load-template")
        screen = app.screen
        assert isinstance(screen, BulkCommentScreen)
        assert "Hello {student_name}" in screen._template_text
        await pilot.press("q")


@pytest.mark.asyncio
async def test_show_recipients_without_data_warns():
    """Clicking Show Recipients before fetching shows a warning."""
    state = _make_app_state()
    app = BulkCommentTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#show-recipients")
        status = app.screen.query_one("#recipient-status", Static)
        assert "Load assignments first" in str(status.render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_send_without_template_warns():
    """Clicking Send without loading a template shows a warning."""
    state = _make_app_state()
    app = BulkCommentTestApp(state)
    async with app.run_test() as pilot:
        await pilot.click("#send-comments")
        status = app.screen.query_one("#send-status", Static)
        assert "Load template first" in str(status.render())
        await pilot.press("q")


# ======================================================================
# Destructive-action confirmation (AGENTS 3.11)
# ======================================================================


async def _bulk_comment_screen_ready(pilot, app) -> BulkCommentScreen:
    """Populate a BulkCommentScreen with one missing student so the
    send path has real data to work with."""
    screen = app.screen
    assert isinstance(screen, BulkCommentScreen)
    screen._template_text = "Hello {student_name}, submit {assignment_name}."
    screen._assignment_summaries = [
        {
            "assignment_id": 1,
            "assignment_name": "Essay 1",
            "points_possible": 100.0,
            "total_submissions": 1,
            "missing_count": 1,
            "missing_students": [{"user_id": 10, "name": "Alice"}],
        },
    ]
    screen._selected_assignment_ids = {1}
    return screen


@pytest.mark.asyncio
async def test_send_button_brings_up_confirmation_modal_first():
    """Clicking Send must open a ConfirmModal before anything is posted."""
    from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal

    state = _make_app_state(canvas_client=MagicMock())
    app = BulkCommentTestApp(state)
    async with app.run_test() as pilot:
        screen = await _bulk_comment_screen_ready(pilot, app)
        await pilot.click("#send-comments")

        modal = app.screen
        assert isinstance(modal, ConfirmModal)
        prompt_text = str(modal.query_one(Label).render())
        assert "Send 1 comments now?" in prompt_text
        # The destructive call must not have happened yet.
        screen.app_state.canvas_client.post_submission_comment.assert_not_called()


@pytest.mark.asyncio
async def test_declining_send_confirmation_posts_nothing():
    """Pressing n on the send-confirmation modal must cancel the send."""
    state = _make_app_state(canvas_client=MagicMock())
    app = BulkCommentTestApp(state)
    async with app.run_test() as pilot:
        screen = await _bulk_comment_screen_ready(pilot, app)
        await pilot.click("#send-comments")
        await pilot.press("n")

        # Back on the bulk comment screen, and nothing was posted.
        screen = app.screen
        assert isinstance(screen, BulkCommentScreen)
        status = screen.query_one("#send-status", Static)
        assert "Cancelled" in str(status.render())
        screen.app_state.canvas_client.post_submission_comment.assert_not_called()


# ======================================================================
# Confirmed sends go through SafeCanvasWriter (AGENTS 3.11)
# ======================================================================


def _send_status(screen: BulkCommentScreen) -> str:
    return str(screen.query_one("#send-status", Static).render())


def test_builtin_template_signs_off_with_a_placeholder_not_a_real_name():
    assert BUILTIN_TEMPLATE.rstrip().endswith("Your instructor")


@pytest.mark.asyncio
async def test_accepting_send_backs_up_then_posts_each_comment(monkeypatch, tmp_path):
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    client = MagicMock()
    client.get_submission.return_value = {"id": 5, "user_id": 10, "score": None}
    client.post_submission_comment.return_value = True
    app = BulkCommentTestApp(_make_app_state(canvas_client=client))
    async with app.run_test() as pilot:
        screen = await _bulk_comment_screen_ready(pilot, app)
        await pilot.click("#send-comments")
        await pilot.press("y")
        await wait_until(pilot, lambda: "Done!" in _send_status(screen), "send finished")
        assert "Sent: 1" in _send_status(screen)
    client.post_submission_comment.assert_called_once_with(
        42, 1, 10, "Hello Alice, submit Essay 1."
    )
    assert len(list(Path(tmp_path).rglob("*.json"))) == 1


@pytest.mark.asyncio
async def test_a_refused_comment_is_counted_as_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    client = MagicMock()
    client.get_submission.return_value = {"id": 5, "user_id": 10, "score": None}
    client.post_submission_comment.return_value = False
    app = BulkCommentTestApp(_make_app_state(canvas_client=client))
    async with app.run_test() as pilot:
        screen = await _bulk_comment_screen_ready(pilot, app)
        await pilot.click("#send-comments")
        await pilot.press("y")
        await wait_until(pilot, lambda: "Done!" in _send_status(screen), "send finished")
        assert "Sent: 0" in _send_status(screen)
        assert "Failed: 1" in _send_status(screen)


@pytest.mark.asyncio
async def test_accepting_send_without_a_workspace_posts_nothing(monkeypatch):
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    client = MagicMock()
    app = BulkCommentTestApp(_make_app_state(canvas_client=client))
    async with app.run_test() as pilot:
        screen = await _bulk_comment_screen_ready(pilot, app)
        await pilot.click("#send-comments")
        await pilot.press("y")
        await wait_until(
            pilot, lambda: "Nothing sent" in _send_status(screen), "send refused"
        )
    client.post_submission_comment.assert_not_called()
