"""Tests for the TUI-6 Grading Workflow Screens.

Uses Textual's ``Pilot`` testing framework to drive screens headlessly.
Each test spins up a minimal ``App`` that mounts the screen under test,
then asserts on behaviour (option selection, table population, modal
confirm/cancel, batch grading flow).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Input, OptionList, Static

from coursewright.core.canvas.models import DiscussionAnalysis, StudentParticipation
from coursewright.ui.tui.screens.grading import (
    BatchGradeScreen,
    GradingOptionsScreen,
    SubmitConfirmModal,
)
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.profile_picker_modal import ProfilePickerModal

# ======================================================================
# Shared fixtures
# ======================================================================


def _make_participation(
    user_id: int = 1,
    name: str = "Alice Student",
    score: float = 85.0,
    meets: bool = True,
) -> StudentParticipation:
    """Create a minimal StudentParticipation for testing."""
    return StudentParticipation(
        user_id=user_id,
        user_name=name,
        total_score=score,
        meets_requirements=meets,
        participation_score=50.0,
        quality_score=35.0,
        total_word_count=400,
        reply_count=2,
        total_posts=3,
        initial_post_word_count=350,
    )


def _make_analysis() -> DiscussionAnalysis:
    """Create a minimal DiscussionAnalysis for testing."""
    return DiscussionAnalysis(
        discussion_id=101,
        discussion_title="Week 3 Discussion",
        course_id=42,
        total_students=3,
        participating_students=3,
        average_score=80.0,
        students_meeting_requirements=2,
        participation_rate=100.0,
        student_participation=[
            _make_participation(1, "Alice Student", 90.0, True),
            _make_participation(2, "Bob Learner", 75.0, True),
            _make_participation(3, "Charlie Newbie", 60.0, False),
        ],
    )


def _make_discussion():
    """Create a mock discussion object."""
    return SimpleNamespace(
        id=101,
        title="Week 3 Discussion",
        assignment_id=201,
        replies_count=15,
        discussion_subentry_count=15,
    )


def _make_app_state():
    """Create a minimal AppState for testing."""
    state = AppState()
    state.canvas_client = MagicMock()
    state.current_course = SimpleNamespace(id=42, name="COURSE 101", course_code="COURSE101")
    state.discussions = [
        SimpleNamespace(
            id=101,
            title="Week 1 Discussion",
            assignment_id=201,
            replies_count=10,
            discussion_subentry_count=10,
        ),
        SimpleNamespace(
            id=102,
            title="Week 2 Discussion",
            assignment_id=202,
            replies_count=8,
            discussion_subentry_count=8,
        ),
        SimpleNamespace(
            id=103,
            title="Week 3 Discussion",
            assignment_id=None,
            replies_count=5,
            discussion_subentry_count=5,
        ),
    ]
    return state


# ======================================================================
# Wrapper apps for Pilot testing
# ======================================================================


class GradingOptionsApp(App):
    """Test app that pushes GradingOptionsScreen."""

    def __init__(self, app_state, analysis, discussion) -> None:
        super().__init__()
        self._state = app_state
        self._analysis = analysis
        self._discussion = discussion

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(GradingOptionsScreen(self._state, self._analysis, self._discussion))


class SubmitConfirmApp(App):
    """Test app that pushes SubmitConfirmModal."""

    def __init__(self, count=3, course="COURSE 101", assignment="HW1"):
        super().__init__()
        self._count = count
        self._course = course
        self._assignment = assignment
        self.modal_result: bool | None = None

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(
            SubmitConfirmModal(
                student_count=self._count,
                course_name=self._course,
                assignment_name=self._assignment,
            ),
            callback=self._on_result,
        )

    def _on_result(self, result: bool) -> None:
        self.modal_result = result
        self.exit()


class BatchGradeApp(App):
    """Test app that pushes BatchGradeScreen."""

    def __init__(self, app_state) -> None:
        super().__init__()
        self._state = app_state

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(BatchGradeScreen(self._state))


# ======================================================================
# GradingOptionsScreen tests
# ======================================================================


@pytest.mark.asyncio
async def test_grading_options_screen_mounts_with_option_list():
    """Screen should display an OptionList with 9 options."""
    state = _make_app_state()
    analysis = _make_analysis()
    discussion = _make_discussion()

    app = GradingOptionsApp(state, analysis, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, GradingOptionsScreen)
        option_list = screen.query_one("#grading-options-list", OptionList)
        assert option_list.option_count == 9


@pytest.mark.asyncio
async def test_grading_options_return_pops_screen():
    """Option 7 (Return) should pop the screen."""
    state = _make_app_state()
    analysis = _make_analysis()
    discussion = _make_discussion()

    app = GradingOptionsApp(state, analysis, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Press 7 → Return to discussion list
        await pilot.press("7")
        await pilot.pause()
        # Should have popped back to the background static
        assert not isinstance(app.screen, GradingOptionsScreen)


@pytest.mark.asyncio
async def test_grading_options_header_shows_discussion_title():
    """The header should include the discussion title."""
    state = _make_app_state()
    analysis = _make_analysis()
    discussion = _make_discussion()

    app = GradingOptionsApp(state, analysis, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        header = screen.query_one("#grading-options-header", Static)
        # The render should contain the discussion title.
        # Static.render() returns a RenderableType; str() or
        # .update text is stored on the widget. Use the internal
        # _content attribute or re-render to text.
        rendered_text = header.render()
        assert "Week 3 Discussion" in str(rendered_text)


# ======================================================================
# SubmitConfirmModal tests
# ======================================================================


@pytest.mark.asyncio
async def test_submit_modal_rejects_wrong_text():
    """Typing 'yes' instead of 'SUBMIT' should not dismiss."""
    app = SubmitConfirmApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#submit-confirm-input", Input)
        inp.value = "yes"
        await pilot.click("#submit-confirm-btn")
        await pilot.pause()
        # Modal should still be active (not dismissed)
        assert isinstance(app.screen, SubmitConfirmModal)


@pytest.mark.asyncio
async def test_submit_modal_accepts_submit_text():
    """Typing 'SUBMIT' and clicking Confirm should dismiss True."""
    app = SubmitConfirmApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#submit-confirm-input", Input)
        inp.value = "SUBMIT"
        await pilot.click("#submit-confirm-btn")
        await pilot.pause()
    assert app.modal_result is True


@pytest.mark.asyncio
async def test_submit_modal_cancel_button():
    """Clicking Cancel should dismiss with False."""
    app = SubmitConfirmApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#submit-cancel-btn")
        await pilot.pause()
    assert app.modal_result is False


@pytest.mark.asyncio
async def test_submit_modal_escape_cancels():
    """Pressing Escape should dismiss with False."""
    app = SubmitConfirmApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.modal_result is False


@pytest.mark.asyncio
async def test_submit_modal_enter_with_submit_text():
    """Pressing Enter in the input with 'SUBMIT' should confirm."""
    app = SubmitConfirmApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#submit-confirm-input", Input)
        inp.value = "SUBMIT"
        # Focus the input and press enter
        inp.focus()
        await pilot.press("enter")
        await pilot.pause()
    assert app.modal_result is True


@pytest.mark.asyncio
async def test_submit_modal_shows_student_count():
    """Modal should display the student count we passed."""
    app = SubmitConfirmApp(count=25, course="CYB 200", assignment="Lab 5")
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, SubmitConfirmModal)
        assert modal._student_count == 25
        assert modal._course_name == "CYB 200"
        assert modal._assignment_name == "Lab 5"


# ======================================================================
# BatchGradeScreen tests
# ======================================================================


@pytest.mark.asyncio
async def test_batch_screen_populates_table():
    """The batch table should show all discussions from app state."""
    state = _make_app_state()
    app = BatchGradeApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, BatchGradeScreen)
        table = screen.query_one("#batch-table", DataTable)
        # 3 discussions in our test state
        assert table.row_count == 3


@pytest.mark.asyncio
async def test_batch_screen_toggle_selection():
    """Pressing Space should toggle the current row's selection."""
    state = _make_app_state()
    app = BatchGradeApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#batch-table", DataTable)
        table.focus()
        # Initially nothing is selected
        assert len(screen._selected_indices) == 0
        # Toggle row 0
        await pilot.press("space")
        await pilot.pause()
        assert 0 in screen._selected_indices
        # Toggle again to deselect
        await pilot.press("space")
        await pilot.pause()
        assert 0 not in screen._selected_indices


@pytest.mark.asyncio
async def test_batch_screen_select_all():
    """Pressing 'a' should select all discussions."""
    state = _make_app_state()
    app = BatchGradeApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#batch-table", DataTable)
        table.focus()
        await pilot.press("a")
        await pilot.pause()
        assert len(screen._selected_indices) == 3
        # Press 'a' again to deselect all
        await pilot.press("a")
        await pilot.pause()
        assert len(screen._selected_indices) == 0


@pytest.mark.asyncio
async def test_batch_screen_no_selection_warns():
    """Pressing Enter with nothing selected should notify."""
    state = _make_app_state()
    app = BatchGradeApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#batch-table", DataTable)
        table.focus()
        # Nothing selected, press Enter
        await pilot.press("enter")
        await pilot.pause()
        # Should still be on the batch screen (no crash)
        assert isinstance(app.screen, BatchGradeScreen)


# ======================================================================
# AnalysisResultsScreen → grading navigation
# ======================================================================


@pytest.mark.asyncio
async def test_analysis_results_g_key_pushes_grading():
    """Pressing 'g' on AnalysisResultsScreen should push grading."""
    from coursewright.ui.tui.screens.analysis_results import (
        AnalysisResultsScreen,
    )

    state = _make_app_state()
    analysis = _make_analysis()
    discussion = _make_discussion()

    class AnalysisApp(App):
        def compose(self) -> ComposeResult:
            yield Static("bg")

        def on_mount(self) -> None:
            self.push_screen(AnalysisResultsScreen(state, analysis, discussion))

    app = AnalysisApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AnalysisResultsScreen)
        await pilot.press("g")
        await pilot.pause()
        assert isinstance(app.screen, GradingOptionsScreen)


@pytest.mark.asyncio
async def test_analysis_results_profile_warns_instead_of_crashing_when_config_unwritable():
    """PermissionError on save_config warns and still updates grading profile in session."""
    from coursewright.ui.tui.screens.analysis_results import (
        AnalysisResultsScreen,
    )

    state = _make_app_state()
    state.config_file = "/readonly/coursewright/config.yaml"
    analysis = _make_analysis()
    discussion = _make_discussion()

    class AnalysisApp(App):
        def compose(self) -> ComposeResult:
            yield Static("bg")

        def on_mount(self) -> None:
            self.push_screen(AnalysisResultsScreen(state, analysis, discussion))

    app = AnalysisApp()
    with (
        patch.object(state, "save_config", side_effect=PermissionError("Permission denied")),
        patch.object(app, "notify") as mock_notify,
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, AnalysisResultsScreen)
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, ProfilePickerModal)
            await pilot.press("2")
            await pilot.pause()
            assert isinstance(app.screen, AnalysisResultsScreen)

    assert state.grading_profile == "legacy"
    assert state.config.discussion.default_grading_profile == "legacy"
    warning_calls = [
        call
        for call in mock_notify.call_args_list
        if call.kwargs.get("severity") == "warning"
        and call.kwargs.get("title") == "Config Not Saved"
    ]
    assert len(warning_calls) == 1
    assert "/readonly/coursewright/config.yaml" in warning_calls[0].args[0]
