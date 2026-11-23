"""Tests for the CyberInsight TUI screens.

Uses Textual's ``Pilot`` testing framework to drive screens headlessly.
Each test spins up a minimal ``App`` that mounts the screen under test,
then asserts on widget presence, keyboard shortcuts, navigation, and
remote-activity visibility via ``TransmissionModal``.

The legacy ``test_grading_option_8_pushes_cyberinsight`` (reaching this
screen from ``GradingOptionsScreen``) lives in ``test_grading_workflow.py``
with the rest of the grading navigation tests.
"""

from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static

from coursewright.core.config.settings import AppConfig, AssistantConfig, LLMConfig
from coursewright.ta.discussions.post_analyzer import (
    AnalysisFlag,
    PostAnalysis,
    PostRanking,
)
from coursewright.ui.tui.screens.cyberinsight import (
    CyberInsightAnalysisScreen,
    DraftReplyScreen,
)
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal
from tests.ui.tui.activity_helpers import wait_until

# ======================================================================
# Shared fixtures
# ======================================================================


def _make_post_analysis(
    post_id: int = 1,
    author: str = "Alice Student",
    merit: int = 8,
    rank: int = 1,
    flags: list | None = None,
) -> PostAnalysis:
    """Create a minimal PostAnalysis for testing."""
    return PostAnalysis(
        post_id=post_id,
        author_name=author,
        word_count=250,
        posted_date=datetime(2025, 11, 21),
        summary="A thoughtful post about cybersecurity.",
        merit_score=merit,
        rank_position=rank,
        flags=flags or [],
        reasoning="Well-structured argument.",
        final_rank_score=merit / 10.0,
    )


def _make_ranking() -> PostRanking:
    """Create a PostRanking with 5 ranked posts and 1 flagged."""
    posts = [
        _make_post_analysis(1, "Alice", 9, 1),
        _make_post_analysis(2, "Bob", 8, 2),
        _make_post_analysis(
            3,
            "Charlie",
            7,
            3,
            [AnalysisFlag.AI_GENERATED],
        ),
        _make_post_analysis(4, "Dana", 6, 4),
        _make_post_analysis(5, "Eve", 5, 5),
    ]
    flagged = [
        _make_post_analysis(
            3,
            "Charlie",
            7,
            3,
            [AnalysisFlag.AI_GENERATED],
        ),
    ]
    return PostRanking(
        discussion_id=101,
        discussion_title="Week 3 Discussion",
        total_posts=5,
        ranked_posts=posts,
        flagged_posts=flagged,
    )


def _make_entries() -> list:
    """Create mock CanvasDiscussionEntry objects."""
    entries = []
    for i in range(1, 6):
        entry = SimpleNamespace(
            id=i,
            user_id=i * 10,
            user_name=["Alice", "Bob", "Charlie", "Dana", "Eve"][i - 1],
            message=f"<p>Post content from student {i}</p>",
            word_count=250,
            created_at=datetime(2025, 11, 21),
        )
        entries.append(entry)
    return entries


def _make_discussion():
    """Create a mock discussion object."""
    return SimpleNamespace(
        id=101,
        title="Week 3 Discussion",
        assignment_id=201,
    )


def _make_app_state():
    """Create a minimal AppState for testing."""
    state = AppState()
    state.canvas_client = MagicMock()
    state.current_course = SimpleNamespace(
        id=42, name="COURSE 101", course_code="COURSE101"
    )
    state.discussions = []
    return state


def _make_enabled_config() -> AppConfig:
    """Return an AppConfig with assistant and LLM enabled."""
    return AppConfig(
        assistant=AssistantConfig(enabled=True),
        llm=LLMConfig(enabled=True, api_key="synthetic-test-key"),
    )


# ======================================================================
# Wrapper apps for Pilot testing
# ======================================================================


class CyberInsightAnalysisApp(App):
    """Test app that pushes CyberInsightAnalysisScreen."""

    def __init__(self, app_state, discussion) -> None:
        super().__init__()
        self._state = app_state
        self._discussion = discussion

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(
            CyberInsightAnalysisScreen(self._state, self._discussion)
        )


class DraftReplyApp(App):
    """Test app that pushes DraftReplyScreen with pre-built data."""

    def __init__(self, app_state, discussion, ranking, entries) -> None:
        super().__init__()
        self._state = app_state
        self._discussion = discussion
        self._ranking = ranking
        self._entries = entries

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(
            DraftReplyScreen(
                self._state,
                self._discussion,
                self._ranking,
                self._entries,
            )
        )


# ======================================================================
# CyberInsightAnalysisScreen tests
# ======================================================================


@pytest.mark.asyncio
async def test_analysis_screen_mounts_with_header():
    """Screen should display the discussion title in header."""
    state = _make_app_state()
    discussion = _make_discussion()

    app = CyberInsightAnalysisApp(state, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CyberInsightAnalysisScreen)
        header = screen.query_one("#cyberinsight-header", Static)
        assert "Week 3 Discussion" in str(header.render())


@pytest.mark.asyncio
async def test_analysis_screen_has_ranked_table():
    """Screen should contain the ranked posts DataTable."""
    state = _make_app_state()
    discussion = _make_discussion()

    app = CyberInsightAnalysisApp(state, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#cyberinsight-ranked-table", DataTable)
        assert table is not None


@pytest.mark.asyncio
async def test_analysis_screen_escape_pops():
    """Pressing Escape should pop back to background."""
    state = _make_app_state()
    discussion = _make_discussion()

    app = CyberInsightAnalysisApp(state, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, CyberInsightAnalysisScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CyberInsightAnalysisScreen)


@pytest.mark.asyncio
async def test_analysis_screen_populate_ranked_table():
    """_populate_ranked_table should fill the table with posts."""
    state = _make_app_state()
    discussion = _make_discussion()

    app = CyberInsightAnalysisApp(state, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CyberInsightAnalysisScreen)

        # Inject ranking data and populate table manually
        screen._ranking = _make_ranking()
        screen._populate_ranked_table()
        await pilot.pause()

        table = screen.query_one("#cyberinsight-ranked-table", DataTable)
        assert table.row_count == 5


@pytest.mark.asyncio
async def test_analysis_screen_flagged_panel_shows_flags():
    """_show_flagged_posts_panel should display flagged posts."""
    state = _make_app_state()
    discussion = _make_discussion()

    app = CyberInsightAnalysisApp(state, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CyberInsightAnalysisScreen)

        screen._ranking = _make_ranking()
        screen._show_flagged_posts_panel()
        await pilot.pause()

        flagged = screen.query_one("#cyberinsight-flagged-panel", Static)
        rendered = str(flagged.render())
        assert "Posts Requiring Attention" in rendered
        assert "Charlie" in rendered


@pytest.mark.asyncio
async def test_analysis_screen_no_flags_shows_green():
    """When no posts are flagged, panel should show all-clear."""
    state = _make_app_state()
    discussion = _make_discussion()

    app = CyberInsightAnalysisApp(state, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # Create ranking with zero flagged posts
        ranking = _make_ranking()
        ranking.flagged_posts = []
        screen._ranking = ranking
        screen._show_flagged_posts_panel()
        await pilot.pause()

        flagged = screen.query_one("#cyberinsight-flagged-panel", Static)
        rendered = str(flagged.render())
        assert "No posts flagged" in rendered


@pytest.mark.asyncio
async def test_analysis_d_key_without_ranking_warns():
    """Pressing d without ranking should warn, not crash."""
    state = _make_app_state()
    discussion = _make_discussion()

    app = CyberInsightAnalysisApp(state, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Ranking is None, pressing d should notify
        await pilot.press("d")
        await pilot.pause()
        # Should still be on the same screen (not crashed)
        assert isinstance(app.screen, CyberInsightAnalysisScreen)


@pytest.mark.asyncio
async def test_analysis_d_key_pushes_draft_screen():
    """Pressing d with ranking should push DraftReplyScreen."""
    state = _make_app_state()
    discussion = _make_discussion()

    app = CyberInsightAnalysisApp(state, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # Inject ranking + entries so d-key works
        screen._ranking = _make_ranking()
        screen._entries = _make_entries()

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, DraftReplyScreen)


@pytest.mark.asyncio
async def test_analysis_worker_shows_and_dismisses_transmission_modal():
    """Remote analysis shows TransmissionModal while running, then dismisses it."""
    state = _make_app_state()
    entries = _make_entries()
    ranking = _make_ranking()
    discussion = _make_discussion()

    fetch_started = threading.Event()
    allow_fetch_finish = threading.Event()

    def slow_get_entries(course_id, discussion_id):
        fetch_started.set()
        allow_fetch_finish.wait(timeout=2.0)
        return entries

    state.canvas_client.get_discussion_entries.side_effect = slow_get_entries

    mock_analyzer = MagicMock()
    mock_analyzer.analyze_discussion_batch.return_value = ranking

    with (
        patch.object(
            state,
            "config",
            _make_enabled_config(),
        ),
        patch(
            "coursewright.ui.tui.screens.cyberinsight.LLMClient",
            return_value=MagicMock(),
        ),
        patch(
            "coursewright.ui.tui.screens.cyberinsight.PostAnalyzer",
            return_value=mock_analyzer,
        ),
    ):
        app = CyberInsightAnalysisApp(state, discussion)
        async with app.run_test() as pilot:
            await wait_until(
                pilot,
                lambda: (
                    fetch_started.is_set()
                    and isinstance(app.screen, TransmissionModal)
                ),
                "TransmissionModal during analysis",
            )
            allow_fetch_finish.set()
            await wait_until(
                pilot,
                lambda: (
                    isinstance(app.screen, CyberInsightAnalysisScreen)
                    and app.screen.query_one(
                        "#cyberinsight-ranked-table", DataTable
                    ).row_count
                    == 5
                ),
                "analysis table populated after modal dismissal",
            )


@pytest.mark.asyncio
async def test_analysis_worker_error_marks_transmission_modal():
    """An analysis error leaves TransmissionModal open in error state."""
    state = _make_app_state()
    discussion = _make_discussion()
    state.canvas_client.get_discussion_entries.side_effect = RuntimeError(
        "Canvas unreachable"
    )

    with (
        patch.object(
            state,
            "config",
            _make_enabled_config(),
        ),
        patch(
            "coursewright.ui.tui.screens.cyberinsight.LLMClient",
            return_value=MagicMock(),
        ),
    ):
        app = CyberInsightAnalysisApp(state, discussion)
        async with app.run_test() as pilot:
            await wait_until(
                pilot,
                lambda: (
                    isinstance(app.screen, TransmissionModal)
                    and app.screen._has_error
                ),
                "TransmissionModal marked with error",
            )


@pytest.mark.asyncio
async def test_plagiarism_check_shows_transmission_modal():
    """Confirmed plagiarism check shows TransmissionModal and updates status."""
    state = _make_app_state()
    discussion = _make_discussion()
    mock_checker = MagicMock()
    mock_checker.check_discussion.return_value = SimpleNamespace(
        suspicious_pairs=[]
    )

    with (
        patch.object(
            state,
            "config",
            _make_enabled_config(),
        ),
        patch(
            "coursewright.ui.tui.screens.cyberinsight.LLMClient",
            return_value=MagicMock(),
        ),
        patch(
            "coursewright.ui.tui.screens.cyberinsight.SimilarityChecker",
            return_value=mock_checker,
        ),
    ):
        app = CyberInsightAnalysisApp(state, discussion)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CyberInsightAnalysisScreen)
            screen._ranking = _make_ranking()
            screen._entries = _make_entries()

            await pilot.press("p")
            await wait_until(
                pilot,
                lambda: isinstance(app.screen, ConfirmModal),
                "plagiarism confirmation modal",
            )
            await pilot.click("#confirm-yes")
            await wait_until(
                pilot,
                lambda: (
                    isinstance(app.screen, CyberInsightAnalysisScreen)
                    and app.screen._plagiarism_checked
                ),
                "plagiarism check completed",
            )


# ======================================================================
# DraftReplyScreen tests
# ======================================================================


@pytest.mark.asyncio
async def test_draft_screen_mounts_with_picker_table():
    """Screen should display a top-5 picker DataTable."""
    state = _make_app_state()
    discussion = _make_discussion()
    ranking = _make_ranking()
    entries = _make_entries()

    app = DraftReplyApp(state, discussion, ranking, entries)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, DraftReplyScreen)
        table = screen.query_one("#draft-picker-table", DataTable)
        assert table.row_count == 5


@pytest.mark.asyncio
async def test_draft_screen_header_shows_discussion():
    """Header should display the discussion title."""
    state = _make_app_state()
    discussion = _make_discussion()
    ranking = _make_ranking()
    entries = _make_entries()

    app = DraftReplyApp(state, discussion, ranking, entries)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        header = screen.query_one("#draft-header", Static)
        assert "Week 3 Discussion" in str(header.render())


@pytest.mark.asyncio
async def test_draft_screen_escape_pops():
    """Pressing Escape should pop back."""
    state = _make_app_state()
    discussion = _make_discussion()
    ranking = _make_ranking()
    entries = _make_entries()

    app = DraftReplyApp(state, discussion, ranking, entries)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DraftReplyScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, DraftReplyScreen)


@pytest.mark.asyncio
async def test_draft_screen_save_without_response_warns():
    """Pressing s without a generated response should warn."""
    state = _make_app_state()
    discussion = _make_discussion()
    ranking = _make_ranking()
    entries = _make_entries()

    app = DraftReplyApp(state, discussion, ranking, entries)
    async with app.run_test() as pilot:
        await pilot.pause()
        # No response generated yet — save should notify
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, DraftReplyScreen)


@pytest.mark.asyncio
async def test_draft_screen_shows_response_panel():
    """_show_response_panel should render response content."""
    state = _make_app_state()
    discussion = _make_discussion()
    ranking = _make_ranking()
    entries = _make_entries()

    app = DraftReplyApp(state, discussion, ranking, entries)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # Simulate a generated response
        mock_response = SimpleNamespace(
            student_name="Alice",
            final_content="Great post, Alice!",
            word_count=4,
            post_id=1,
        )
        screen._show_response_panel(mock_response)
        await pilot.pause()

        panel = screen.query_one("#draft-response-panel", Static)
        rendered = str(panel.render())
        assert "Alice" in rendered
        assert "Great post" in rendered


@pytest.mark.asyncio
async def test_draft_screen_pick_post_generates_response_with_modal():
    """Selecting post 1 shows TransmissionModal and renders the draft."""
    state = _make_app_state()
    discussion = _make_discussion()
    ranking = _make_ranking()
    entries = _make_entries()

    mock_response = SimpleNamespace(
        student_name="Alice",
        final_content="Thoughtful analysis, Alice!",
        word_count=3,
        post_id=1,
    )
    mock_generator = MagicMock()
    mock_generator.generate_response.return_value = mock_response

    with (
        patch.object(
            state,
            "config",
            _make_enabled_config(),
        ),
        patch(
            "coursewright.ui.tui.screens.cyberinsight.LLMClient",
            return_value=MagicMock(),
        ),
        patch(
            "coursewright.ui.tui.screens.cyberinsight.ResponseGenerator",
            return_value=mock_generator,
        ),
    ):
        app = DraftReplyApp(state, discussion, ranking, entries)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("1")
            await wait_until(
                pilot,
                lambda: (
                    isinstance(app.screen, DraftReplyScreen)
                    and app.screen._response_draft is not None
                ),
                "draft response rendered",
            )
            panel = app.screen.query_one("#draft-response-panel", Static)
            assert "Thoughtful analysis, Alice!" in str(panel.render())
