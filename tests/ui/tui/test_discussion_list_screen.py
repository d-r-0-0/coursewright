"""Tests for DiscussionListScreen, the first step of the grading path.

The source suite had no tests for this screen, so these are new.  They
drive the screen with Textual's pilot and a fake Canvas client:

* discussions load under the transmission log and fill the table;
* a failed load or analysis marks the log as an error (it can only be
  dismissed then), so the user is never stuck behind "Fetching...";
* selecting a discussion runs the analysis and opens
  ``AnalysisResultsScreen``; ``g`` then opens ``GradingOptionsScreen``,
  and from there CyberInsight and its draft-reply screen are reachable;
* ``e`` opens the bulk export and ``p`` the grading-profile picker.

Every name, id and course here is synthetic.
"""

from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import DataTable

from coursewright.core.canvas.models import DiscussionAnalysis, StudentParticipation
from coursewright.core.config.settings import AppConfig, AssistantConfig, LLMConfig
from coursewright.ta.discussions.post_analyzer import PostAnalysis, PostRanking
from coursewright.ui.tui.screens.analysis_results import AnalysisResultsScreen
from coursewright.ui.tui.screens.cyberinsight import (
    CyberInsightAnalysisScreen,
    DraftReplyScreen,
)
from coursewright.ui.tui.screens.discussion_list import DiscussionListScreen
from coursewright.ui.tui.screens.export import BulkExportScreen
from coursewright.ui.tui.screens.grading import GradingOptionsScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.profile_picker_modal import ProfilePickerModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal
from tests.ui.tui.activity_helpers import OneScreenApp, find_screen, wait_until

# ======================================================================
# Synthetic data
# ======================================================================


def make_discussions() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=101,
            title="Week 1 Discussion",
            discussion_type="threaded",
            replies_count=4,
            posted_at=datetime(2026, 1, 12),
            assignment_id=201,
        ),
        SimpleNamespace(
            id=102,
            title="Week 2 Discussion",
            discussion_type=None,
            replies_count=None,
            discussion_subentry_count=2,
            posted_at=None,
            assignment_id=None,
        ),
    ]


def make_analysis() -> DiscussionAnalysis:
    return DiscussionAnalysis(
        discussion_id=101,
        discussion_title="Week 1 Discussion",
        course_id=42,
        total_students=1,
        participating_students=1,
        average_score=80.0,
        students_meeting_requirements=1,
        participation_rate=100.0,
        student_participation=[
            StudentParticipation(
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
        ],
    )


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
        discussion_title="Week 1 Discussion",
        total_posts=3,
        ranked_posts=posts,
        flagged_posts=[],
    )


def make_state(canvas_client=None) -> AppState:
    state = AppState()
    state.canvas_client = canvas_client or MagicMock()
    if canvas_client is None:
        state.canvas_client.get_discussions.return_value = make_discussions()
    state.current_course = SimpleNamespace(id=42, name="COURSE 101", course_code="COURSE101")
    return state


def discussion_list_app(state: AppState) -> OneScreenApp:
    return OneScreenApp(DiscussionListScreen(state), state)


async def wait_for_loaded_list(app, pilot) -> DiscussionListScreen:
    await wait_until(
        pilot,
        lambda: (
            isinstance(app.screen, DiscussionListScreen)
            and app.screen.query_one("#discussion-table", DataTable).row_count > 0
        ),
        "discussion table populated",
    )
    return app.screen


# ======================================================================
# Loading
# ======================================================================


@pytest.mark.asyncio
async def test_discussions_load_under_the_transmission_log_then_fill_the_table():
    fetch_started = threading.Event()
    allow_fetch_finish = threading.Event()
    client = MagicMock()

    def slow_get_discussions(course_id):
        fetch_started.set()
        allow_fetch_finish.wait(timeout=2.0)
        return make_discussions()

    client.get_discussions.side_effect = slow_get_discussions
    state = make_state(client)
    app = discussion_list_app(state)
    async with app.run_test() as pilot:
        await wait_until(
            pilot,
            lambda: fetch_started.is_set() and isinstance(app.screen, TransmissionModal),
            "transmission log while discussions load",
        )
        allow_fetch_finish.set()
        screen = await wait_for_loaded_list(app, pilot)
        table = screen.query_one("#discussion-table", DataTable)
        assert table.row_count == 2
        first_row = [str(cell) for cell in table.get_row_at(0)]
        assert first_row == ["1", "Week 1 Discussion", "Threaded", "4", "01/12/2026", "Yes"]
        second_row = [str(cell) for cell in table.get_row_at(1)]
        assert second_row == ["2", "Week 2 Discussion", "Threaded", "2", "Unknown", "No"]
    client.get_discussions.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_failed_load_marks_the_log_as_an_error_so_escape_closes_it():
    client = MagicMock()
    client.get_discussions.side_effect = RuntimeError("synthetic outage")
    app = discussion_list_app(make_state(client))
    async with app.run_test() as pilot:
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, TransmissionModal) and app.screen._has_error,
            "transmission log in error state",
        )
        await pilot.press("escape")
        await wait_until(
            pilot, lambda: isinstance(app.screen, DiscussionListScreen), "back on the list"
        )
        status = str(app.screen.query_one("#discussion-status").render())
        assert "synthetic outage" in status


# ======================================================================
# Selection -> analysis -> grading -> CyberInsight
# ======================================================================


def patched_analyzer(analysis):
    analyzer = MagicMock()
    analyzer.analyze_discussion.return_value = analysis
    return patch("coursewright.ta.grading.analyzer.DiscussionAnalyzer", return_value=analyzer)


@pytest.mark.asyncio
async def test_selecting_a_discussion_runs_analysis_and_opens_results():
    state = make_state()
    with patched_analyzer(make_analysis()) as analyzer_class:
        app = discussion_list_app(state)
        async with app.run_test() as pilot:
            await wait_for_loaded_list(app, pilot)
            await pilot.press("1")
            await wait_until(
                pilot,
                lambda: isinstance(app.screen, AnalysisResultsScreen),
                "AnalysisResultsScreen pushed",
            )
            assert app.screen.discussion.id == 101
    analyzer = analyzer_class.return_value
    assert analyzer.analyze_discussion.call_args.args == (42, 101)
    grading_config = analyzer_class.call_args.args[1]
    assert grading_config["profile"] == state.grading_profile


@pytest.mark.asyncio
async def test_failed_analysis_marks_the_log_as_an_error():
    state = make_state()
    with patched_analyzer(None):
        app = discussion_list_app(state)
        async with app.run_test() as pilot:
            await wait_for_loaded_list(app, pilot)
            await pilot.press("2")
            await wait_until(
                pilot,
                lambda: isinstance(app.screen, TransmissionModal) and app.screen._has_error,
                "analysis log in error state",
            )
            assert find_screen(app, AnalysisResultsScreen) is None


@pytest.mark.asyncio
async def test_discussion_to_grading_to_cyberinsight_to_draft_reply():
    """The whole path: list -> results -> grading -> CyberInsight -> draft."""
    state = make_state()
    state.canvas_client.get_discussion_entries.return_value = [
        SimpleNamespace(id=index, user_id=index, user_name=f"Student {index}", message="<p>x</p>")
        for index in range(1, 4)
    ]
    post_analyzer = MagicMock()
    post_analyzer.analyze_discussion_batch.return_value = make_ranking()
    enabled_config = AppConfig(
        assistant=AssistantConfig(enabled=True),
        llm=LLMConfig(enabled=True, api_key="synthetic-test-key"),
    )
    cyberinsight = "coursewright.ui.tui.screens.cyberinsight"
    with (
        patched_analyzer(make_analysis()),
        patch.object(state, "config", enabled_config),
        patch(f"{cyberinsight}.LLMClient", return_value=MagicMock()),
        patch(f"{cyberinsight}.PostAnalyzer", return_value=post_analyzer),
    ):
        app = discussion_list_app(state)
        async with app.run_test() as pilot:
            await wait_for_loaded_list(app, pilot)
            await pilot.press("1")
            await wait_until(
                pilot, lambda: isinstance(app.screen, AnalysisResultsScreen), "results"
            )
            await pilot.press("g")
            await wait_until(
                pilot, lambda: isinstance(app.screen, GradingOptionsScreen), "grading menu"
            )
            await pilot.press("8")
            await wait_until(
                pilot,
                lambda: (
                    isinstance(app.screen, CyberInsightAnalysisScreen)
                    and app.screen._ranking is not None
                ),
                "CyberInsight analysis finished",
            )
            await pilot.press("d")
            await wait_until(
                pilot, lambda: isinstance(app.screen, DraftReplyScreen), "DraftReplyScreen"
            )
            assert app.screen.discussion.id == 101


# ======================================================================
# Other keys
# ======================================================================


@pytest.mark.asyncio
async def test_e_opens_bulk_export_for_the_loaded_discussions():
    state = make_state()
    with patch("coursewright.core.export.markdown_exporter.MarkdownExporter"):
        app = discussion_list_app(state)
        async with app.run_test() as pilot:
            await wait_for_loaded_list(app, pilot)
            await pilot.press("e")
            await wait_until(
                pilot, lambda: find_screen(app, BulkExportScreen) is not None, "bulk export"
            )


@pytest.mark.asyncio
async def test_p_opens_the_profile_picker_and_remembers_the_choice():
    state = make_state()
    app = discussion_list_app(state)
    async with app.run_test() as pilot:
        await wait_for_loaded_list(app, pilot)
        await pilot.press("p")
        await wait_until(pilot, lambda: isinstance(app.screen, ProfilePickerModal), "picker")
        await pilot.press("2")
        await wait_until(
            pilot, lambda: isinstance(app.screen, DiscussionListScreen), "back on the list"
        )
    assert state.grading_profile == "legacy"


@pytest.mark.asyncio
async def test_p_warns_instead_of_crashing_when_config_unwritable():
    state = make_state()
    state.config_file = "/readonly/coursewright/config.yaml"
    app = discussion_list_app(state)
    with (
        patch.object(state, "save_config", side_effect=PermissionError("Permission denied")),
        patch.object(app, "notify") as mock_notify,
    ):
        async with app.run_test() as pilot:
            await wait_for_loaded_list(app, pilot)
            await pilot.press("p")
            await wait_until(pilot, lambda: isinstance(app.screen, ProfilePickerModal), "picker")
            await pilot.press("2")
            await wait_until(
                pilot, lambda: isinstance(app.screen, DiscussionListScreen), "back on the list"
            )
            status = str(app.screen.query_one("#discussion-status").render())
            assert "(session only; config not saved)" in status

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
