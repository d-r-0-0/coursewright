"""Tests for the ZeroUngradedScreen TUI screen."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Label, Static

from coursewright.core.canvas.models import Assignment, CanvasUser, Submission
from coursewright.ui.tui.screens.zero_ungraded import ZeroUngradedScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal


class FakeCourse:
    """Minimal course object for zero-ungraded screen tests."""

    def __init__(self, course_id: int = 42, name: str = "Forensics") -> None:
        self.id = course_id
        self.name = name


class ZeroUngradedTestApp(App):
    """Minimal app that pushes ZeroUngradedScreen."""

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(ZeroUngradedScreen(self.app_state))


class FakeCanvasClient:
    """Canvas fake exposing the reads and the grade write zeroing uses.

    Each assignment has one ungraded submission (user ``100 + id``) and
    one already graded (user ``200 + id``), so only the first is zeroed.
    """

    def __init__(self, assignments: list[Assignment]) -> None:
        self.assignments = assignments
        self.grades_sent: list[tuple[int, int, int, float]] = []

    def get_assignments(self, _course_id: int) -> list[Assignment]:
        return self.assignments

    def get_assignment_submissions(self, _course_id: int, assignment_id: int):
        return [
            SimpleNamespace(user_id=100 + assignment_id, score=None, excused=False),
            SimpleNamespace(user_id=200 + assignment_id, score=9.0, excused=False),
        ]

    def get_submission(self, _course_id: int, assignment_id: int, user_id: int):
        return {"assignment_id": assignment_id, "user_id": user_id, "score": None}

    def submit_grade(self, course_id, assignment_id, user_id, score, comment=None):
        self.grades_sent.append((course_id, assignment_id, user_id, score))
        return {"user_id": user_id, "score": score}


class BlockingLoadCanvasClient(FakeCanvasClient):
    """Canvas fake that pauses assignment loading until released."""

    def __init__(
        self,
        assignments: list[Assignment],
        started_event: Event,
        release_event: Event,
    ) -> None:
        super().__init__(assignments)
        self.started_event = started_event
        self.release_event = release_event

    def get_assignments(self, course_id: int) -> list[Assignment]:
        self.started_event.set()
        self.release_event.wait(timeout=2)
        return super().get_assignments(course_id)


class BlockingZeroCanvasClient(FakeCanvasClient):
    """Canvas fake that pauses the zero-out call until released."""

    def __init__(
        self,
        assignments: list[Assignment],
        started_event: Event,
        release_event: Event,
    ) -> None:
        super().__init__(assignments)
        self.started_event = started_event
        self.release_event = release_event

    def get_assignment_submissions(self, course_id: int, assignment_id: int):
        self.started_event.set()
        self.release_event.wait(timeout=2)
        return super().get_assignment_submissions(course_id, assignment_id)


def make_assignment(assignment_id: int = 1, *, published: bool = True) -> Assignment:
    """Build a past-due assignment for screen tests."""
    return Assignment(
        id=assignment_id,
        name=f"Lab {assignment_id}",
        due_at=datetime.now(UTC) - timedelta(days=1),
        published=published,
    )


def make_state(canvas_client) -> AppState:
    """Build AppState with a fake course and Canvas client."""
    state = AppState()
    state.current_course = FakeCourse()
    state.canvas_client = canvas_client
    return state


async def wait_for_zero_screen(app: ZeroUngradedTestApp, pilot) -> ZeroUngradedScreen:
    """Wait for transient modals to close and return the zero screen."""
    for _attempt_index in range(40):
        if isinstance(app.screen, ZeroUngradedScreen):
            return app.screen
        await pilot.pause(0.05)
    raise AssertionError(f"Expected ZeroUngradedScreen, got {type(app.screen).__name__}")


@pytest.mark.asyncio
async def test_assignment_load_shows_canvas_api_transmission_modal():
    """Initial assignment loading should show the shared API traffic modal."""
    started_event = Event()
    release_event = Event()
    client = BlockingLoadCanvasClient(
        [make_assignment()],
        started_event,
        release_event,
    )

    app = ZeroUngradedTestApp(make_state(client))
    async with app.run_test() as pilot:
        for _attempt_index in range(40):
            if started_event.is_set() and isinstance(app.screen, TransmissionModal):
                break
            await pilot.pause(0.05)

        assert isinstance(app.screen, TransmissionModal)
        status_text = str(app.screen.query_one("#transmission-status", Static).render())
        assert "Fetching assignments from Canvas" in status_text

        release_event.set()
        screen = await wait_for_zero_screen(app, pilot)
        assert screen.query_one("#zero-table") is not None


@pytest.mark.asyncio
async def test_zero_selected_shows_canvas_api_transmission_modal(monkeypatch, tmp_path):
    """Zeroing submissions should show the shared API traffic modal."""
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    started_event = Event()
    release_event = Event()
    client = BlockingZeroCanvasClient(
        [make_assignment()],
        started_event,
        release_event,
    )

    app = ZeroUngradedTestApp(make_state(client))
    async with app.run_test() as pilot:
        screen = await wait_for_zero_screen(app, pilot)
        screen._selected_ids = {1}
        screen._zero_selected_worker()

        for _attempt_index in range(40):
            if started_event.is_set() and isinstance(app.screen, TransmissionModal):
                break
            await pilot.pause(0.05)

        assert isinstance(app.screen, TransmissionModal)
        status_text = str(app.screen.query_one("#transmission-status", Static).render())
        assert "Zeroing ungraded submissions in Canvas" in status_text

        release_event.set()
        screen = await wait_for_zero_screen(app, pilot)
        result_text = str(screen.query_one("#zero-result", Static).render())
        assert "Zeroed" in result_text
    # Only the ungraded submission was zeroed, after its backup was saved.
    assert client.grades_sent == [(42, 1, 101, 0)]
    assert len(list(Path(tmp_path).rglob("*.json"))) == 1


# ======================================================================
# Destructive-action confirmation (AGENTS 3.11)
# ======================================================================


@pytest.mark.asyncio
async def test_zero_key_brings_up_confirmation_modal_first():
    """Pressing z must open a ConfirmModal before anything is zeroed."""
    from unittest.mock import MagicMock

    from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal

    client = MagicMock()
    app = ZeroUngradedTestApp(make_state(client))
    async with app.run_test() as pilot:
        screen = await wait_for_zero_screen(app, pilot)
        screen._selected_ids = {1}
        await pilot.press("z")

        modal = app.screen
        assert isinstance(modal, ConfirmModal)
        prompt_text = str(modal.query_one(Label).render())
        assert "assignment(s)?" in prompt_text
        assert "set to 0" in prompt_text
        # The destructive call must not have happened yet.
        client.submit_grade.assert_not_called()
        client.get_assignment_submissions.assert_not_called()


@pytest.mark.asyncio
async def test_declining_confirmation_keeps_submissions_untouched():
    """Pressing n on the zero-confirmation modal must cancel the zero."""
    from unittest.mock import MagicMock

    client = MagicMock()
    app = ZeroUngradedTestApp(make_state(client))
    async with app.run_test() as pilot:
        screen = await wait_for_zero_screen(app, pilot)
        screen._selected_ids = {1}
        await pilot.press("z")
        await pilot.press("n")

        # Back on the zero screen, and nothing was zeroed.
        screen = await wait_for_zero_screen(app, pilot)
        client.submit_grade.assert_not_called()
        client.get_assignment_submissions.assert_not_called()


@pytest.mark.asyncio
async def test_accepting_confirmation_zeroes_through_the_safe_writer(monkeypatch, tmp_path):
    """Pressing z then y zeroes every selected assignment, each backed up."""
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    client = FakeCanvasClient([make_assignment(1), make_assignment(2)])
    app = ZeroUngradedTestApp(make_state(client))
    async with app.run_test() as pilot:
        screen = await wait_for_zero_screen(app, pilot)
        screen._selected_ids = {1, 2}
        await pilot.press("z")
        await pilot.press("y")
        for _attempt_index in range(60):
            if "Zeroed" in str(screen.query_one("#zero-result", Static).render()):
                break
            await pilot.pause(0.05)
        result_text = str(screen.query_one("#zero-result", Static).render())
        assert "Zeroed" in result_text
        assert "2" in result_text
    assert client.grades_sent == [(42, 1, 101, 0), (42, 2, 102, 0)]
    assert len(list(Path(tmp_path).rglob("*.json"))) == 2


@pytest.mark.asyncio
async def test_zeroing_without_a_workspace_writes_nothing(monkeypatch):
    """No private workspace means no backups, so nothing may be zeroed."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    client = FakeCanvasClient([make_assignment(1)])
    app = ZeroUngradedTestApp(make_state(client))
    async with app.run_test() as pilot:
        screen = await wait_for_zero_screen(app, pilot)
        screen._selected_ids = {1}
        screen._zero_selected_worker()
        for _attempt_index in range(40):
            if isinstance(app.screen, TransmissionModal) and app.screen._has_error:
                break
            await pilot.pause(0.05)
        assert isinstance(app.screen, TransmissionModal)
        status_text = str(app.screen.query_one("#transmission-status", Static).render())
        assert "COURSEWRIGHT_WORKSPACE" in status_text
    assert client.grades_sent == []


@pytest.mark.asyncio
async def test_unpublished_assignments_are_hidden_and_never_zeroed(monkeypatch, tmp_path):
    """Unpublished assignments are skipped so zeroing never hits a Canvas 403."""
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    client = FakeCanvasClient(
        [
            make_assignment(1, published=True),
            make_assignment(2, published=False),
        ]
    )
    app = ZeroUngradedTestApp(make_state(client))
    async with app.run_test() as pilot:
        screen = await wait_for_zero_screen(app, pilot)
        table = screen.query_one("#zero-table", DataTable)
        assert table.row_count == 1
        assert "1 unpublished hidden" in str(screen.query_one("#zero-status", Static).render())

        await pilot.press("p")
        assert screen._selected_ids == {1}

        await pilot.press("a")
        assert screen._selected_ids == {1}

        await pilot.press("z")
        await pilot.press("y")
        for _attempt_index in range(60):
            if "Zeroed" in str(screen.query_one("#zero-result", Static).render()):
                break
            await pilot.pause(0.05)

    assert client.grades_sent == [(42, 1, 101, 0)]
    assert len(list(Path(tmp_path).rglob("*.json"))) == 1


@pytest.mark.asyncio
async def test_zero_skips_test_student_and_submitted_submissions(monkeypatch, tmp_path):
    """TUI zeroing excludes non-roster users (e.g. Test Student) and submitted work."""
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))

    class GuardedCanvasClient(FakeCanvasClient):
        def get_course_users(self, _course_id: int) -> list[CanvasUser]:
            return [
                CanvasUser(id=101, name="Alex Rivera"),
                CanvasUser(id=102, name="Jordan Patel"),
            ]

        def get_assignment_submissions(self, _course_id: int, _assignment_id: int):
            return [
                Submission(id=1, user_id=101, score=None, workflow_state="unsubmitted"),
                Submission(
                    id=2,
                    user_id=102,
                    score=None,
                    workflow_state="submitted",
                    submitted_at=datetime.now(UTC) - timedelta(hours=1),
                ),
                Submission(id=3, user_id=999, score=None, workflow_state="unsubmitted"),
            ]

    client = GuardedCanvasClient([make_assignment(1)])
    app = ZeroUngradedTestApp(make_state(client))
    async with app.run_test() as pilot:
        screen = await wait_for_zero_screen(app, pilot)
        screen._selected_ids = {1}
        await pilot.press("z")
        await pilot.press("y")
        for _attempt_index in range(60):
            if "Zeroed" in str(screen.query_one("#zero-result", Static).render()):
                break
            await pilot.pause(0.05)

    assert client.grades_sent == [(42, 1, 101, 0)]
    assert len(list(Path(tmp_path).rglob("*.json"))) == 1


@pytest.mark.asyncio
async def test_zero_falls_back_when_get_course_users_fails(monkeypatch, tmp_path):
    """If get_course_users raises, TUI zeroing falls back gracefully without crashing."""
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))

    class BrokenRosterCanvasClient(FakeCanvasClient):
        def get_course_users(self, _course_id: int):
            raise RuntimeError("roster lookup failed")

    client = BrokenRosterCanvasClient([make_assignment(1)])
    app = ZeroUngradedTestApp(make_state(client))
    async with app.run_test() as pilot:
        screen = await wait_for_zero_screen(app, pilot)
        screen._selected_ids = {1}
        await pilot.press("z")
        await pilot.press("y")
        for _attempt_index in range(60):
            if "Zeroed" in str(screen.query_one("#zero-result", Static).render()):
                break
            await pilot.pause(0.05)

    assert client.grades_sent == [(42, 1, 101, 0)]
