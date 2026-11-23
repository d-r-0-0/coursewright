"""Focused tests for GradePreviewScreen feedback detail behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from coursewright.ui.tui.screens.grading import FeedbackDetailModal, GradePreviewScreen
from coursewright.ui.tui.state import AppState


class DeterministicGradePreviewScreen(GradePreviewScreen):
    """Test subclass that skips worker startup for deterministic UI tests."""

    def on_mount(self) -> None:
        """Skip asynchronous grade preparation worker in this test harness."""

    def _prepare_grades(self) -> str:  # type: ignore[override]
        """No-op replacement for the @work-decorated parent method.

        UX-6 wired ``_prepare_grades`` through a TransmissionModal-backed
        worker.  Tests that pre-populate ``_grades_to_submit`` directly do
        not want that worker to fire (it would push the modal on top of
        ``GradePreviewScreen`` and obscure the row-cursor assertions).
        """
        return "ok"

class GradePreviewTestApp(App):
    """Minimal app for driving GradePreviewScreen with Textual Pilot."""

    def __init__(self) -> None:
        super().__init__()
        self._app_state = AppState()
        self._app_state.canvas_client = MagicMock()
        self._discussion = SimpleNamespace(id=101, title="Week 3 Discussion")
        self._analysis = SimpleNamespace(course_id=42, student_participation=[])

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(
            DeterministicGradePreviewScreen(
                app_state=self._app_state,
                analysis=self._analysis,
                discussion=self._discussion,
                use_llm=True,
            )
        )


@pytest.mark.asyncio
async def test_enter_on_grade_preview_row_opens_feedback_modal_with_full_comment():
    """Enter on a row should open feedback modal and render full comment text."""
    app = GradePreviewTestApp()

    full_feedback_comment = (
        "AI feedback summary:\n"
        "- Score breakdown: participation 45/50, quality 40/50\n"
        "- Rationale: strong evidence, clear peer engagement,"
        " and specific examples"
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, GradePreviewScreen)

        screen._grades_to_submit = [
            {
                "student_id": 1,
                "student_name": "Alice Student",
                "score": 85.0,
                "max_points": 100.0,
                "meets_requirements": True,
                "comment": full_feedback_comment,
            }
        ]
        screen._populate_preview_table()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, FeedbackDetailModal)
        rendered_feedback = app.screen.query_one("#feedback-detail-comment", Static).render()
        assert "Score breakdown" in str(rendered_feedback)
        assert "Rationale" in str(rendered_feedback)
