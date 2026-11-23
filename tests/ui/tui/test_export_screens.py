"""Tests for the TUI-7 Export Screens.

Uses Textual's ``Pilot`` testing framework to drive each export screen
headlessly.  All Canvas API calls and file-system writes are mocked so
tests run without network or disk side-effects.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, LoadingIndicator, OptionList, Static

from coursewright.core.canvas.models import (
    DiscussionAnalysis,
    StudentParticipation,
)
from coursewright.ui.tui.screens.export import (
    BulkExportScreen,
    ExportFormatScreen,
    MarkdownExportScreen,
)
from coursewright.ui.tui.state import AppState
from tests.ui.tui.activity_helpers import wait_until

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
    """Create a minimal AppState with mock Canvas client."""
    state = AppState()
    state.canvas_client = MagicMock()
    state.canvas_client.get_discussion_entries.return_value = []
    state.current_course = SimpleNamespace(
        id=42, name="COURSE 101", course_code="COURSE101"
    )
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
    ]
    return state


# ======================================================================
# Wrapper apps for Pilot testing
# ======================================================================


class BulkExportApp(App):
    """Test app that pushes BulkExportScreen."""

    def __init__(self, app_state) -> None:
        super().__init__()
        self._state = app_state

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(BulkExportScreen(self._state))


class MarkdownExportApp(App):
    """Test app that pushes MarkdownExportScreen."""

    def __init__(self, app_state, analysis, discussion) -> None:
        super().__init__()
        self._state = app_state
        self._analysis = analysis
        self._discussion = discussion

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(
            MarkdownExportScreen(
                self._state, self._analysis, self._discussion
            )
        )


class ExportFormatApp(App):
    """Test app that pushes ExportFormatScreen."""

    def __init__(self, app_state, analysis) -> None:
        super().__init__()
        self._state = app_state
        self._analysis = analysis

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        self.push_screen(
            ExportFormatScreen(self._state, self._analysis)
        )


# ======================================================================
# BulkExportScreen tests
# ======================================================================


@pytest.mark.asyncio
async def test_bulk_export_screen_mounts_with_table():
    """Screen should display a DataTable with the correct columns."""
    state = _make_app_state()

    # Mock the exporter so no files are written
    with patch(
        "coursewright.core.export.markdown_exporter.MarkdownExporter"
    ) as mock_exporter_cls:
        mock_exporter = MagicMock()
        mock_exporter.export_discussion_to_markdown.return_value = (
            "/tmp/exports/test.md"
        )
        mock_exporter_cls.return_value = mock_exporter

        app = BulkExportApp(state)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, BulkExportScreen)
            table = screen.query_one(
                "#bulk-export-table", DataTable
            )
            # Should have columns set up
            assert len(table.columns) == 4


@pytest.mark.asyncio
async def test_bulk_export_screen_header_shows_course_name():
    """Header should include the course name and discussion count."""
    state = _make_app_state()

    with patch(
        "coursewright.core.export.markdown_exporter.MarkdownExporter"
    ):
        app = BulkExportApp(state)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            header = screen.query_one(
                "#bulk-export-header", Static
            )
            rendered = str(header.render())
            assert "COURSE 101" in rendered
            assert "2" in rendered  # 2 discussions


@pytest.mark.asyncio
async def test_bulk_export_worker_calls_exporter():
    """The worker should call export_discussion_to_markdown for each
    discussion and populate the results table."""
    state = _make_app_state()

    with patch(
        "coursewright.core.export.markdown_exporter.MarkdownExporter"
    ) as mock_exporter_cls:
        mock_exporter = MagicMock()
        mock_exporter.export_discussion_to_markdown.return_value = (
            "/tmp/exports/test.md"
        )
        mock_exporter_cls.return_value = mock_exporter

        app = BulkExportApp(state)
        async with app.run_test() as pilot:
            # Give the worker time to complete
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            # Should have called the exporter once per discussion
            assert (
                mock_exporter.export_discussion_to_markdown.call_count
                == 2
            )


# ======================================================================
# MarkdownExportScreen tests
# ======================================================================


@pytest.mark.asyncio
async def test_markdown_export_screen_mounts():
    """Screen should display the discussion title and options."""
    state = _make_app_state()
    analysis = _make_analysis()
    discussion = _make_discussion()

    app = MarkdownExportApp(state, analysis, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MarkdownExportScreen)

        header = screen.query_one("#md-export-header", Static)
        rendered = str(header.render())
        assert "Week 3 Discussion" in rendered


@pytest.mark.asyncio
async def test_markdown_export_toggle_analysis():
    """Pressing 'a' should toggle the include-analysis flag."""
    state = _make_app_state()
    analysis = _make_analysis()
    discussion = _make_discussion()

    app = MarkdownExportApp(state, analysis, discussion)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MarkdownExportScreen)

        # Initially off
        assert screen._include_analysis is False

        # Toggle on
        await pilot.press("a")
        await pilot.pause()
        assert screen._include_analysis is True

        # Toggle off again
        await pilot.press("a")
        await pilot.pause()
        assert screen._include_analysis is False


@pytest.mark.asyncio
async def test_markdown_export_runs_worker():
    """Pressing 'e' should trigger the export worker."""
    state = _make_app_state()
    analysis = _make_analysis()
    discussion = _make_discussion()

    with patch(
        "coursewright.core.export.markdown_exporter.MarkdownExporter"
    ) as mock_exporter_cls:
        mock_exporter = MagicMock()
        mock_exporter.export_discussion_to_markdown.return_value = (
            "/tmp/exports/test.md"
        )
        mock_exporter_cls.return_value = mock_exporter

        app = MarkdownExportApp(state, analysis, discussion)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("e")
            # Give the worker time to complete
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            # Exporter should have been called
            assert (
                mock_exporter.export_discussion_to_markdown.called
            )


@pytest.mark.asyncio
async def test_markdown_export_prevents_double_export():
    """After exporting, pressing 'e' again should not re-export."""
    state = _make_app_state()
    analysis = _make_analysis()
    discussion = _make_discussion()

    with patch(
        "coursewright.core.export.markdown_exporter.MarkdownExporter"
    ) as mock_exporter_cls:
        mock_exporter = MagicMock()
        mock_exporter.export_discussion_to_markdown.return_value = (
            "/tmp/exports/test.md"
        )
        mock_exporter_cls.return_value = mock_exporter

        app = MarkdownExportApp(state, analysis, discussion)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            # Press 'e' a second time
            await pilot.press("e")
            await pilot.pause()

            # Should only have exported once
            assert (
                mock_exporter.export_discussion_to_markdown.call_count
                == 1
            )


# ======================================================================
# ExportFormatScreen tests
# ======================================================================


@pytest.mark.asyncio
async def test_export_format_screen_mounts_with_option_list():
    """Screen should display an OptionList with 2 format options."""
    state = _make_app_state()
    analysis = _make_analysis()

    app = ExportFormatApp(state, analysis)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ExportFormatScreen)
        option_list = screen.query_one(
            "#format-option-list", OptionList
        )
        assert option_list.option_count == 2


@pytest.mark.asyncio
async def test_export_format_screen_header_shows_discussion():
    """Header should include the discussion title and student count."""
    state = _make_app_state()
    analysis = _make_analysis()

    app = ExportFormatApp(state, analysis)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        header = screen.query_one(
            "#format-export-header", Static
        )
        rendered = str(header.render())
        assert "Week 3 Discussion" in rendered
        assert "3" in rendered  # 3 students


@pytest.mark.asyncio
async def test_export_format_csv_selection():
    """Pressing '1' should trigger CSV export."""
    state = _make_app_state()
    analysis = _make_analysis()

    with patch(
        "coursewright.core.export.csv_exporter.CSVExporter"
    ) as mock_csv_cls:
        mock_csv = MagicMock()
        mock_csv.export_analysis.return_value = (
            "/tmp/exports/grades.csv"
        )
        mock_csv_cls.return_value = mock_csv

        app = ExportFormatApp(state, analysis)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            assert mock_csv.export_analysis.called


@pytest.mark.asyncio
async def test_export_format_excel_selection():
    """Pressing '2' should trigger Excel export."""
    state = _make_app_state()
    analysis = _make_analysis()

    with patch(
        "coursewright.core.export.excel_exporter.ExcelExporter"
    ) as mock_xlsx_cls:
        mock_xlsx = MagicMock()
        mock_xlsx.export_analysis.return_value = (
            "/tmp/exports/grades.xlsx"
        )
        mock_xlsx_cls.return_value = mock_xlsx

        app = ExportFormatApp(state, analysis)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            assert mock_xlsx.export_analysis.called


@pytest.mark.asyncio
async def test_export_format_prevents_double_export():
    """After exporting, selecting again should not re-export."""
    state = _make_app_state()
    analysis = _make_analysis()

    with patch(
        "coursewright.core.export.csv_exporter.CSVExporter"
    ) as mock_csv_cls:
        mock_csv = MagicMock()
        mock_csv.export_analysis.return_value = (
            "/tmp/exports/grades.csv"
        )
        mock_csv_cls.return_value = mock_csv

        app = ExportFormatApp(state, analysis)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            # Press '1' again
            await pilot.press("1")
            await pilot.pause()

            # Should only have exported once
            assert mock_csv.export_analysis.call_count == 1


@pytest.mark.asyncio
async def test_export_format_escape_goes_back():
    """Pressing Escape should pop back to the previous screen."""
    state = _make_app_state()
    analysis = _make_analysis()

    app = ExportFormatApp(state, analysis)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ExportFormatScreen)
        await pilot.press("escape")
        await pilot.pause()
        # Should have popped back
        assert not isinstance(app.screen, ExportFormatScreen)


# ======================================================================
# Remote activity is visible (docs/HOUSE_STYLE.md section 3)
# ======================================================================


@pytest.mark.asyncio
async def test_markdown_export_shows_loading_indicator_while_canvas_is_read():
    """The entry fetch runs under a visible LoadingIndicator, hidden after."""
    state = _make_app_state()
    fetch_started = threading.Event()
    allow_fetch_finish = threading.Event()

    def slow_get_entries(course_id, discussion_id):
        fetch_started.set()
        allow_fetch_finish.wait(timeout=2.0)
        return []

    state.canvas_client.get_discussion_entries.side_effect = slow_get_entries

    with patch("coursewright.core.export.markdown_exporter.MarkdownExporter") as exporter_class:
        exporter_class.return_value.export_discussion_to_markdown.return_value = "out.md"
        app = MarkdownExportApp(state, _make_analysis(), _make_discussion())
        async with app.run_test() as pilot:
            await pilot.pause()
            indicator = app.screen.query_one("#md-export-loading", LoadingIndicator)
            assert indicator.display is False
            await pilot.press("e")
            await wait_until(pilot, fetch_started.is_set, "entry fetch started")
            assert indicator.display is True
            allow_fetch_finish.set()
            await wait_until(pilot, lambda: indicator.display is False, "indicator hidden")


@pytest.mark.asyncio
async def test_bulk_export_hides_loading_indicator_when_done():
    """Bulk export animates while it reads Canvas and stops when finished."""
    state = _make_app_state()
    fetch_started = threading.Event()
    allow_fetch_finish = threading.Event()

    def slow_get_entries(course_id, discussion_id):
        fetch_started.set()
        allow_fetch_finish.wait(timeout=2.0)
        return []

    state.canvas_client.get_discussion_entries.side_effect = slow_get_entries

    with patch("coursewright.core.export.markdown_exporter.MarkdownExporter") as exporter_class:
        exporter_class.return_value.export_discussion_to_markdown.return_value = "out.md"
        app = BulkExportApp(state)
        async with app.run_test() as pilot:
            await wait_until(pilot, fetch_started.is_set, "bulk fetch started")
            indicator = app.screen.query_one("#bulk-export-loading", LoadingIndicator)
            assert indicator.display is True
            allow_fetch_finish.set()
            await wait_until(pilot, lambda: indicator.display is False, "indicator hidden")
