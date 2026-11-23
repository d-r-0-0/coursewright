"""The grading workflow beyond the menus the source suite covers.

* From ``GradingOptionsScreen`` the two CyberInsight options open
  ``CyberInsightAnalysisScreen``, and from there ``d`` opens
  ``DraftReplyScreen`` (the source's option 8 test, plus option 9 and the
  draft-reply hop).
* Submitting grades goes through ``SafeCanvasWriter``: a backup lands in the
  private workspace before every grade, a refused grade is counted and
  named, and with no workspace nothing is sent (AGENTS.md 3.11).
* ``p`` opens ``ProfilePickerModal`` and the choice is saved to config.

Every name, id and course here is synthetic.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from textual.app import App, ComposeResult
from textual.widgets import Static

from coursewright.core.canvas.models import DiscussionAnalysis, StudentParticipation
from coursewright.core.config.settings import AppConfig, AssistantConfig, LLMConfig
from coursewright.ta.discussions.post_analyzer import PostAnalysis, PostRanking
from coursewright.ui.tui.screens.cyberinsight import (
    CyberInsightAnalysisScreen,
    DraftReplyScreen,
)
from coursewright.ui.tui.screens.grading import GradePreviewScreen, GradingOptionsScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.profile_picker_modal import ProfilePickerModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal
from tests.ui.tui.activity_helpers import wait_until

# ======================================================================
# Synthetic data
# ======================================================================


def make_analysis() -> DiscussionAnalysis:
    participation = StudentParticipation(
        user_id=1,
        user_name="Student One",
        total_score=80.0,
        meets_requirements=True,
        participation_score=50.0,
        quality_score=30.0,
        total_word_count=400,
        reply_count=2,
        total_posts=3,
        initial_post_word_count=350,
    )
    return DiscussionAnalysis(
        discussion_id=101,
        discussion_title="Week 3 Discussion",
        course_id=42,
        total_students=1,
        participating_students=1,
        average_score=80.0,
        students_meeting_requirements=1,
        participation_rate=100.0,
        student_participation=[participation],
    )


def make_discussion() -> SimpleNamespace:
    return SimpleNamespace(id=101, title="Week 3 Discussion", assignment_id=201)


def make_app_state() -> AppState:
    state = AppState()
    state.canvas_client = MagicMock()
    state.current_course = SimpleNamespace(id=42, name="COURSE 101", course_code="COURSE101")
    state.discussions = []
    return state


def make_ranking() -> PostRanking:
    posts = [
        PostAnalysis(
            post_id=index,
            author_name=f"Student {index}",
            word_count=250,
            posted_date=None,
            summary="A synthetic post.",
            merit_score=10 - index,
            rank_position=index,
            flags=[],
            reasoning="Synthetic reasoning.",
            final_rank_score=(10 - index) / 10.0,
        )
        for index in range(1, 4)
    ]
    return PostRanking(
        discussion_id=101,
        discussion_title="Week 3 Discussion",
        total_posts=3,
        ranked_posts=posts,
        flagged_posts=[],
    )


def make_entries() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=index,
            user_id=index * 10,
            user_name=f"Student {index}",
            message=f"<p>Synthetic post {index}</p>",
            word_count=250,
            created_at=None,
        )
        for index in range(1, 4)
    ]


class GradingOptionsApp(App):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.app_state = state

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(GradingOptionsScreen(self.app_state, make_analysis(), make_discussion()))


# ======================================================================
# GradingOptionsScreen -> CyberInsight -> DraftReply
# ======================================================================


def cyberinsight_patches(state: AppState, ranking: PostRanking):
    """Patch CyberInsight's config, LLM and analyzer with synthetic fakes."""
    analyzer = MagicMock()
    analyzer.analyze_discussion_batch.return_value = ranking
    enabled_config = AppConfig(
        assistant=AssistantConfig(enabled=True),
        llm=LLMConfig(enabled=True, api_key="synthetic-test-key"),
    )
    module = "coursewright.ui.tui.screens.cyberinsight"
    return (
        patch.object(state, "config", enabled_config),
        patch(f"{module}.LLMClient", return_value=MagicMock()),
        patch(f"{module}.PostAnalyzer", return_value=analyzer),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("option_key", ["8", "9"])
async def test_cyberinsight_options_push_analysis_screen(option_key):
    """Options 8 and 9 (CyberInsight) open the analysis screen, as in the source."""
    state = make_app_state()
    state.canvas_client.get_discussion_entries.return_value = make_entries()
    config_patch, llm_patch, analyzer_patch = cyberinsight_patches(state, make_ranking())
    with config_patch, llm_patch, analyzer_patch:
        app = GradingOptionsApp(state)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, GradingOptionsScreen)
            await pilot.press(option_key)
            await wait_until(
                pilot,
                lambda: any(isinstance(s, CyberInsightAnalysisScreen) for s in app.screen_stack),
                "CyberInsightAnalysisScreen pushed",
            )


@pytest.mark.asyncio
async def test_grading_reaches_draft_reply_through_cyberinsight():
    """Grading menu -> CyberInsight analysis -> d -> DraftReplyScreen."""
    state = make_app_state()
    state.canvas_client.get_discussion_entries.return_value = make_entries()
    config_patch, llm_patch, analyzer_patch = cyberinsight_patches(state, make_ranking())
    with config_patch, llm_patch, analyzer_patch:
        app = GradingOptionsApp(state)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("8")
            await wait_until(
                pilot,
                lambda: (
                    isinstance(app.screen, CyberInsightAnalysisScreen)
                    and app.screen._ranking is not None
                ),
                "analysis finished and its modal dismissed",
            )
            analysis_screen = app.screen
            assert analysis_screen.discussion.id == 101
            await pilot.press("d")
            await wait_until(
                pilot, lambda: isinstance(app.screen, DraftReplyScreen), "DraftReplyScreen"
            )
            assert app.screen.discussion.id == 101


# ======================================================================
# Submitting grades through SafeCanvasWriter
# ======================================================================


class QuietGradePreviewScreen(GradePreviewScreen):
    """A preview whose grades are set by the test rather than prepared."""

    def _prepare_grades(self) -> str:  # type: ignore[override]
        """Skip the prepare-grades worker; the test fills the rows.

        Textual runs the parent's ``on_mount`` too, so the worker it
        starts is the thing to replace, not ``on_mount`` itself.
        """
        return "ok"


class GradePreviewApp(App):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.app_state = state

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        preview = QuietGradePreviewScreen(self.app_state, make_analysis(), make_discussion())
        self.push_screen(preview)


def prepared_grades() -> list[dict]:
    return [
        {"student_id": 1, "student_name": "Student One", "score": 80.0, "comment": "Good"},
        {"student_id": 2, "student_name": "Student Two", "score": 70.0, "comment": "Fine"},
    ]


async def start_submission(app: GradePreviewApp, pilot) -> QuietGradePreviewScreen:
    await pilot.pause()
    screen = app.screen
    assert isinstance(screen, QuietGradePreviewScreen)
    screen._assignment = {"id": 201, "name": "Week 3 Discussion", "points_possible": 100}
    screen._grades_to_submit = prepared_grades()
    # The user typed SUBMIT in SubmitConfirmModal.
    screen._handle_submit_confirmed(True)
    return screen


@pytest.mark.asyncio
async def test_confirmed_submit_backs_up_each_grade_then_sends_it(monkeypatch, tmp_path):
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    state = make_app_state()
    state.canvas_client.get_submission.return_value = {"id": 9, "score": None}
    state.canvas_client.submit_grade.return_value = {"id": 9, "score": 80.0}
    app = GradePreviewApp(state)
    async with app.run_test() as pilot:
        screen = await start_submission(app, pilot)
        await wait_until(
            pilot,
            lambda: "Submitted 2 grades" in str(screen.query_one("#preview-status").render()),
            "submission finished",
        )
    sent_students = [call.args[2] for call in state.canvas_client.submit_grade.call_args_list]
    assert sent_students == [1, 2]
    backups = sorted(Path(tmp_path).rglob("*.json"))
    assert len(backups) == 2


@pytest.mark.asyncio
async def test_a_refused_grade_is_counted_and_named_and_the_rest_continue(monkeypatch, tmp_path):
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    state = make_app_state()
    state.canvas_client.get_submission.return_value = {"id": 9, "score": None}
    # Canvas refuses the first grade (the client reports False) and takes the second.
    state.canvas_client.submit_grade.side_effect = [False, {"id": 9, "score": 70.0}]
    app = GradePreviewApp(state)
    async with app.run_test() as pilot:
        screen = await start_submission(app, pilot)
        await wait_until(
            pilot,
            lambda: "1 failed" in str(screen.query_one("#preview-status").render()),
            "submission finished with one failure",
        )
        status = str(screen.query_one("#preview-status").render())
    assert "Submitted 1 grades" in status
    assert "Student One" in status
    assert state.canvas_client.submit_grade.call_count == 2


@pytest.mark.asyncio
async def test_submit_without_a_workspace_sends_nothing_and_says_so(monkeypatch):
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    state = make_app_state()
    app = GradePreviewApp(state)
    async with app.run_test() as pilot:
        screen = await start_submission(app, pilot)
        await wait_until(
            pilot,
            lambda: "Submission error" in str(screen.query_one("#preview-status").render()),
            "submission refused",
        )
        assert isinstance(app.screen, TransmissionModal)
    state.canvas_client.submit_grade.assert_not_called()


# ======================================================================
# Profile picker
# ======================================================================


@pytest.mark.asyncio
async def test_profile_key_opens_picker_and_saves_choice():
    """p opens ProfilePickerModal; picking AI saves it and updates the header."""
    state = make_app_state()
    app = GradingOptionsApp(state)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ProfilePickerModal)
        await pilot.press("3")
        await pilot.pause()
        assert isinstance(app.screen, GradingOptionsScreen)
        header = str(app.screen.query_one("#grading-options-header", Static).render())
        assert "ai_discussion" in header
    assert state.grading_profile == "ai_discussion"
    # The suite runs each test from its own empty directory (tests/conftest.py).
    saved = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    assert saved["discussion"]["default_grading_profile"] == "ai_discussion"


@pytest.mark.asyncio
async def test_profile_key_warns_instead_of_crashing_when_config_unwritable():
    """PermissionError on save_config warns and still applies profile in session."""
    state = make_app_state()
    state.config_file = "/readonly/coursewright/config.yaml"
    app = GradingOptionsApp(state)
    with (
        patch.object(state, "save_config", side_effect=PermissionError("Permission denied")),
        patch.object(app, "notify") as mock_notify,
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            await wait_until(pilot, lambda: isinstance(app.screen, ProfilePickerModal), "picker")
            await pilot.press("3")
            await wait_until(
                pilot,
                lambda: isinstance(app.screen, GradingOptionsScreen),
                "back on grading options",
            )
            header = str(app.screen.query_one("#grading-options-header", Static).render())
            assert "ai_discussion" in header

    assert state.grading_profile == "ai_discussion"
    assert state.config.discussion.default_grading_profile == "ai_discussion"
    warning_calls = [
        call
        for call in mock_notify.call_args_list
        if call.kwargs.get("severity") == "warning"
        and call.kwargs.get("title") == "Config Not Saved"
    ]
    assert len(warning_calls) == 1
    assert "/readonly/coursewright/config.yaml" in warning_calls[0].args[0]


@pytest.mark.parametrize("maximum, expected", [(50, 40), (200, 160)])
async def test_preview_scales_after_feedback_role_review(maximum, expected):
    from tests.ui.tui.activity_helpers import OneScreenApp

    state = make_app_state()
    state.canvas_client.get_assignment_by_discussion.return_value = {
        "id": 201,
        "name": "Synthetic assignment",
        "points_possible": maximum,
    }
    analysis = make_analysis()
    analysis.student_participation[0].total_score = 60
    screen = GradePreviewScreen(state, analysis, make_discussion(), use_llm=False)

    def review(_gradebook, participation, *_args):
        participation.total_score = 80
        return "Reviewed synthetic rubric score: 80/100"

    screen._build_comment = review
    async with OneScreenApp(screen, state).run_test() as pilot:
        await wait_until(pilot, lambda: bool(screen._grades_to_submit), "grade preview")
        assert screen._grades_to_submit[0]["score"] == expected
