"""Tests for the TUI GradeLiner wizard screen.

Uses Textual's ``Pilot`` testing framework to drive the five-step
GradeLiner wizard headlessly.  All Canvas API calls, LLM calls, and
file-system operations are mocked so tests run without network or
disk side-effects.

The tests walk through the wizard setup flow:
  1. File detection / auto-populate (via ``core.inbox``)
  2. Orchestrator initialization
  3. Mapping display (cached path) and manual editor
  4. Coverage loading + assignment selection
  5. Preview generation + smoke test
  6. Confirmation + submission with spinner
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, DataTable, Input, Static

from coursewright.core.inbox import CandidateLocation, InboxCandidate
from coursewright.ta.gradeliner.models import (
    AssignmentMapping,
    GradeImportRow,
    StudentMapping,
)
from coursewright.ui.tui.screens.gradeliner import (
    NETACAD_IMPORT_DIR,
    NETACAD_IMPORT_DIR_LEGACY,
    AssignmentMappingEditorScreen,
    GradeLinerScreen,
    _find_newest_netacad_csv,
)
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

# ======================================================================
# Shared fixtures
# ======================================================================

SAMPLE_CSV_PATH = Path("exports/sample_export.csv")


def _make_app_state() -> AppState:
    """Create a minimal AppState with a mock Canvas client and course."""
    state = AppState()
    state.canvas_client = MagicMock()
    state.llm_client = MagicMock()
    state.current_course = SimpleNamespace(
        id=42,
        name="Example Networking Course",
        course_code="NET101",
    )
    return state


def _make_assignment_mappings() -> list[AssignmentMapping]:
    """Create sample assignment mappings for testing."""
    return [
        AssignmentMapping(
            netacad_column="Checkpoint Exam: Modules 1-3",
            canvas_assignment_name="U03T01: Modules 1-3 Exam",
            canvas_assignment_id=1001,
            canvas_points_possible=100.0,
            confidence="high",
        ),
        AssignmentMapping(
            netacad_column="Checkpoint Exam: Modules 4-7",
            canvas_assignment_name="U05T01: Modules 4-7 Exam",
            canvas_assignment_id=1002,
            canvas_points_possible=100.0,
            confidence="medium",
        ),
    ]


def _make_student_mappings() -> list[StudentMapping]:
    """Create sample student mappings for testing."""
    return [
        StudentMapping(
            netacad_name="Alice Student",
            netacad_email="alice@example.com",
            canvas_user_id=501,
            canvas_name="Alice Student",
            canvas_login_id="astudent",
            match_method="exact",
            confidence="high",
        ),
        StudentMapping(
            netacad_name="Bob Learner",
            netacad_email="bob@example.com",
            canvas_user_id=502,
            canvas_name="Robert Learner",
            canvas_login_id="blearner",
            match_method="fuzzy",
            confidence="medium",
        ),
    ]


def _make_coverage_entries() -> list[dict]:
    """Create sample assignment coverage data for testing."""
    return [
        {
            "canvas_assignment_id": 1001,
            "canvas_assignment_name": "U03T01: Modules 1-3 Exam",
            "netacad_column": "Checkpoint Exam: Modules 1-3",
            "students_with_scores": 18,
            "total_students": 19,
            "score_percentage": 94.7,
        },
        {
            "canvas_assignment_id": 1002,
            "canvas_assignment_name": "U05T01: Modules 4-7 Exam",
            "netacad_column": "Checkpoint Exam: Modules 4-7",
            "students_with_scores": 0,
            "total_students": 19,
            "score_percentage": 0.0,
        },
    ]


def _make_import_rows() -> list[GradeImportRow]:
    """Create sample grade import rows for preview testing."""
    return [
        GradeImportRow(
            canvas_user_id=501,
            canvas_name="Alice Student",
            assignment_id=1001,
            assignment_name="U03T01: Modules 1-3 Exam",
            netacad_score=85.0,
            scaled_score=85.0,
        ),
        GradeImportRow(
            canvas_user_id=502,
            canvas_name="Bob Learner",
            assignment_id=1001,
            assignment_name="U03T01: Modules 1-3 Exam",
            netacad_score=73.0,
            scaled_score=73.0,
        ),
    ]


# ======================================================================
# Wrapper app for Pilot testing
# ======================================================================


class GradeLinerTestApp(App):
    """Test app that pushes GradeLinerScreen onto the screen stack."""

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self._state = app_state

    def compose(self) -> ComposeResult:
        # No widgets in the base app; GradeLinerScreen is pushed on mount
        return []

    def on_mount(self) -> None:
        self.push_screen(GradeLinerScreen(self._state))


async def wait_for_gradeliner_screen(
    app: GradeLinerTestApp,
    pilot,
) -> GradeLinerScreen:
    """Wait for transient modals to close and return the GradeLiner screen."""
    for _attempt_index in range(40):
        if isinstance(app.screen, GradeLinerScreen):
            return app.screen
        await pilot.pause(0.05)
    raise AssertionError(f"Expected GradeLinerScreen, got {type(app.screen).__name__}")


async def wait_for_confirm_modal(
    app: GradeLinerTestApp,
    pilot,
) -> ConfirmModal:
    """Wait until ConfirmModal is active on the screen stack."""
    for _attempt_index in range(40):
        if isinstance(app.screen, ConfirmModal):
            return app.screen
        await pilot.pause(0.05)
    raise AssertionError(f"Expected ConfirmModal, got {type(app.screen).__name__}")


# ======================================================================
# Tests
# ======================================================================


@pytest.mark.asyncio
async def test_gradeliner_screen_mounts_all_steps():
    """Verify the wizard renders all five step sections on mount."""
    state = _make_app_state()
    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)

        async with app.run_test():
            step_file = app.screen.query_one("#step-file")
            step_mapping = app.screen.query_one("#step-mapping")
            step_select = app.screen.query_one("#step-select")
            step_preview = app.screen.query_one("#step-preview")
            step_submit = app.screen.query_one("#step-submit")

            assert step_file is not None
            assert step_mapping is not None
            assert step_select is not None
            assert step_preview is not None
            assert step_submit is not None
            mapping_button = app.screen.query_one("#run-mapping", Button)
            assert str(mapping_button.label) == "Load/Generate Mappings"
            editor_button = app.screen.query_one("#edit-assignment-mappings", Button)
            assert editor_button.disabled is True


@pytest.mark.asyncio
async def test_gradeliner_file_detection_populates_input():
    """Verify auto-detection fills the CSV path input on mount."""
    state = _make_app_state()

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=SAMPLE_CSV_PATH,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test():
            csv_input = app.screen.query_one("#csv-path-input", Input)
            assert csv_input.value == str(SAMPLE_CSV_PATH)

            file_status = app.screen.query_one("#file-status", Static)
            assert "Detected" in str(file_status.render())


@pytest.mark.asyncio
async def test_gradeliner_file_detection_no_file_found():
    """Verify clicking 'Use Detected File' with no CSV shows a warning."""
    state = _make_app_state()

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            app.screen._detect_file()
            await pilot.pause()

            file_status = app.screen.query_one("#file-status", Static)
            rendered_text = str(file_status.render())
            assert "No CSV" in rendered_text or "not found" in rendered_text.lower()


def test_gradeliner_find_newest_netacad_csv_uses_core_inbox():
    """Verify NetAcad file discovery delegates to core.inbox.find_exported_file."""
    older_csv = InboxCandidate(
        path=Path("/tmp/downloads/netacad_old.csv"),
        found_in=CandidateLocation.DOWNLOADS,
        modified_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    newer_tsv = InboxCandidate(
        path=Path("/tmp/downloads/netacad_new.tsv"),
        found_in=CandidateLocation.DOWNLOADS,
        modified_at=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
    )

    def fake_find_exported_file(
        pattern: str,
        *,
        workspace_import_directory: Path | None = None,
    ):
        assert workspace_import_directory == NETACAD_IMPORT_DIR
        if pattern == "*.csv":
            return older_csv
        if pattern == "*.tsv":
            return newer_tsv
        return None

    with patch(
        "coursewright.ui.tui.screens.gradeliner.find_exported_file",
        side_effect=fake_find_exported_file,
    ):
        assert _find_newest_netacad_csv() == newer_tsv.path


def test_gradeliner_find_newest_netacad_csv_falls_back_to_import_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When Downloads is empty, check netacad-import/ then legacy GradeLinErUpper/."""
    empty_downloads = tmp_path / "downloads"
    empty_downloads.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "coursewright.core.inbox.discovery.default_download_directory",
        lambda: empty_downloads,
    )

    legacy_dir = tmp_path / NETACAD_IMPORT_DIR_LEGACY
    legacy_dir.mkdir()
    legacy_csv = legacy_dir / "legacy_export.csv"
    legacy_csv.write_text("student,score\n", encoding="utf-8")

    assert _find_newest_netacad_csv() == legacy_csv.relative_to(tmp_path)

    import_dir = tmp_path / NETACAD_IMPORT_DIR
    import_dir.mkdir()
    import_csv = import_dir / "netacad_export.csv"
    import_csv.write_text("student,score\n", encoding="utf-8")

    assert _find_newest_netacad_csv() == import_csv.relative_to(tmp_path)


@pytest.mark.asyncio
async def test_gradeliner_initialize_orchestrator():
    """Verify the orchestrator initializes when a valid file is given."""
    state = _make_app_state()

    mock_orchestrator_class = MagicMock()
    mock_orchestrator_instance = MagicMock()
    mock_orchestrator_instance.course_name = "NET101 Example Networking"
    mock_orchestrator_class.return_value = mock_orchestrator_instance

    with (
        patch(
            "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
            return_value=None,
        ),
        patch(
            "coursewright.ui.tui.screens.gradeliner.GradeLinerUpper",
            mock_orchestrator_class,
        ),
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            csv_input = app.screen.query_one("#csv-path-input", Input)
            csv_input.value = str(SAMPLE_CSV_PATH)
            await pilot.pause()

            with patch.object(Path, "exists", return_value=True):
                app.screen._initialize_orchestrator()
            screen = await wait_for_gradeliner_screen(app, pilot)

            file_status = screen.query_one("#file-status", Static)
            rendered_text = str(file_status.render())
            assert "Initialized" in rendered_text


@pytest.mark.asyncio
async def test_gradeliner_cached_mapping_path():
    """Verify cached mappings are detected and skip LLM calls."""
    state = _make_app_state()

    mock_orchestrator = MagicMock()
    mock_orchestrator.course_name = "NET101"
    mock_orchestrator.has_cached_mappings.return_value = True

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._orchestrator = mock_orchestrator

            await pilot.click("#run-mapping")

            mapping_status = app.screen.query_one("#mapping-status", Static)
            rendered_text = str(mapping_status.render())
            assert "cached" in rendered_text.lower()


@pytest.mark.asyncio
async def test_gradeliner_manual_assignment_mapping_correction():
    """Verify the editor maps a Canvas assignment to a new NetAcad column."""
    assignment_mappings = _make_assignment_mappings()
    corrected_column = "Final Exam: Modules 10-14"
    netacad_columns = [mapping.netacad_column for mapping in assignment_mappings] + [
        corrected_column
    ]
    saved_mappings: list[AssignmentMapping] = []

    def save_mappings(mappings: list[AssignmentMapping]) -> None:
        saved_mappings.clear()
        saved_mappings.extend(mappings)

    class MappingEditorTestApp(App):
        def compose(self) -> ComposeResult:
            return []

        def on_mount(self) -> None:
            self.push_screen(
                AssignmentMappingEditorScreen(
                    mappings=assignment_mappings,
                    netacad_columns=netacad_columns,
                    save_callback=save_mappings,
                )
            )

    app = MappingEditorTestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, AssignmentMappingEditorScreen)

        mapping_table = screen.query_one("#editor-mapping-table", DataTable)
        netacad_table = screen.query_one("#editor-netacad-table", DataTable)
        mapping_table.move_cursor(row=1, scroll=False)
        await pilot.pause()
        netacad_table.move_cursor(row=2, scroll=False)
        screen.action_apply_mapping()
        await pilot.pause()

        assert saved_mappings[1].netacad_column == corrected_column
        assert saved_mappings[1].confidence == "manual"


@pytest.mark.asyncio
async def test_gradeliner_opens_assignment_mapping_editor():
    """Verify GradeLiner launches the full-screen mapping editor."""
    state = _make_app_state()
    assignment_mappings = _make_assignment_mappings()

    mock_orchestrator = MagicMock()
    mock_orchestrator.save_assignment_mappings.return_value = None

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._orchestrator = mock_orchestrator
            screen._remember_netacad_assignment_options(assignment_mappings)
            screen._render_assignment_mapping_table(assignment_mappings)

            screen._open_assignment_mapping_editor()
            await pilot.pause()

            assert isinstance(app.screen, AssignmentMappingEditorScreen)


@pytest.mark.asyncio
async def test_gradeliner_coverage_loading():
    """Verify coverage loading shows API modal and populates selection table."""
    state = _make_app_state()
    coverage_entries = _make_coverage_entries()
    started_event = Event()
    release_event = Event()

    def slow_summarize_assignment_coverage():
        started_event.set()
        release_event.wait(timeout=2)
        return coverage_entries

    mock_orchestrator = MagicMock()
    mock_orchestrator.course_name = "NET101"
    mock_orchestrator.summarize_assignment_coverage.side_effect = (
        slow_summarize_assignment_coverage
    )

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._orchestrator = mock_orchestrator

            await pilot.click("#load-coverage")

            for _attempt_index in range(40):
                if started_event.is_set() and isinstance(app.screen, TransmissionModal):
                    break
                await pilot.pause(0.05)

            assert isinstance(app.screen, TransmissionModal)
            modal_status = app.screen.query_one("#transmission-status", Static)
            assert "Loading assignment coverage" in str(modal_status.render())

            release_event.set()
            screen = await wait_for_gradeliner_screen(app, pilot)
            coverage_status = screen.query_one("#coverage-status", Static)
            rendered_text = str(coverage_status.render())
            assert "2 mapped assignments" in rendered_text


@pytest.mark.asyncio
async def test_gradeliner_preview_generation():
    """Verify grade preview shows API modal and runs smoke tests."""
    state = _make_app_state()
    import_rows = _make_import_rows()
    started_event = Event()
    release_event = Event()

    def slow_prepare_grade_import(selected_assignment_ids=None):
        started_event.set()
        release_event.wait(timeout=2)
        return import_rows

    mock_orchestrator = MagicMock()
    mock_orchestrator.course_name = "NET101"
    mock_orchestrator.prepare_grade_import.side_effect = slow_prepare_grade_import
    mock_orchestrator.validator = MagicMock()
    mock_orchestrator.validator.perform_smoke_tests.return_value = (
        True,
        "All checks passed",
    )

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._orchestrator = mock_orchestrator
            screen._selected_assignment_ids = {1001}

            await pilot.click("#prepare-preview")

            for _attempt_index in range(40):
                if started_event.is_set() and isinstance(app.screen, TransmissionModal):
                    break
                await pilot.pause(0.05)

            assert isinstance(app.screen, TransmissionModal)
            modal_status = app.screen.query_one("#transmission-status", Static)
            assert "Preparing grade preview" in str(modal_status.render())

            release_event.set()
            screen = await wait_for_gradeliner_screen(app, pilot)

            preview_table = screen.query_one("#preview-table", DataTable)
            assert preview_table.row_count == 2

            smoke_status = screen.query_one("#smoke-status", Static)
            rendered_text = str(smoke_status.render())
            assert "passed" in rendered_text.lower()


@pytest.mark.asyncio
async def test_gradeliner_submit_success():
    """Verify submission requires confirmation, executes with execute=True, and shows result."""
    state = _make_app_state()
    import_rows = _make_import_rows()

    mock_orchestrator = MagicMock()
    mock_orchestrator.course_name = "NET101"
    mock_orchestrator.submit_grades.return_value = {
        "successful": 2,
        "failed": 0,
    }

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._orchestrator = mock_orchestrator
            screen._import_rows = import_rows

            await pilot.click("#submit-grades")
            await wait_for_confirm_modal(app, pilot)
            await pilot.click("#confirm-yes")
            screen = await wait_for_gradeliner_screen(app, pilot)

            mock_orchestrator.submit_grades.assert_called_once()
            _, kwargs = mock_orchestrator.submit_grades.call_args
            assert kwargs.get("execute") is True

            submit_status = screen.query_one("#submit-status", Static)
            rendered_text = str(submit_status.render())
            assert "2 successful" in rendered_text
            assert "0 failed" in rendered_text


@pytest.mark.asyncio
async def test_gradeliner_submit_cancelled_skips_submission():
    """Verify cancelling ConfirmModal aborts submission without writing."""
    state = _make_app_state()
    import_rows = _make_import_rows()

    mock_orchestrator = MagicMock()
    mock_orchestrator.course_name = "NET101"

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._orchestrator = mock_orchestrator
            screen._import_rows = import_rows

            await pilot.click("#submit-grades")
            await wait_for_confirm_modal(app, pilot)
            await pilot.click("#confirm-no")
            screen = await wait_for_gradeliner_screen(app, pilot)

            mock_orchestrator.submit_grades.assert_not_called()
            submit_status = screen.query_one("#submit-status", Static)
            assert "cancelled" in str(submit_status.render()).lower()


@pytest.mark.asyncio
async def test_gradeliner_submit_disabled_during_submission():
    """Verify the submit button is disabled while submission runs."""
    state = _make_app_state()
    import_rows = _make_import_rows()

    started = Event()
    release = Event()

    def slow_submit(rows, progress_callback=None, execute=False):
        started.set()
        release.wait(10)
        return {"successful": 2, "failed": 0}

    mock_orchestrator = MagicMock()
    mock_orchestrator.course_name = "NET101"
    mock_orchestrator.submit_grades.side_effect = slow_submit

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._orchestrator = mock_orchestrator
            screen._import_rows = import_rows

            await pilot.click("#submit-grades")
            await wait_for_confirm_modal(app, pilot)
            await pilot.click("#confirm-yes")

            for _ in range(100):
                if started.is_set():
                    break
                await pilot.pause()
            assert started.is_set()

            submit_button = screen.query_one("#submit-grades", Button)
            assert submit_button.disabled is True

            release.set()
            for _ in range(100):
                if not submit_button.disabled:
                    break
                await pilot.pause()
            assert submit_button.disabled is False


@pytest.mark.asyncio
async def test_gradeliner_submit_without_init_shows_error():
    """Verify submitting without initializing shows an error."""
    state = _make_app_state()

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            await pilot.click("#submit-grades")

            submit_status = app.screen.query_one("#submit-status", Static)
            rendered_text = str(submit_status.render())
            assert "Initialize first" in rendered_text


@pytest.mark.asyncio
async def test_gradeliner_submit_without_preview_shows_warning():
    """Verify submitting without preview rows shows a warning."""
    state = _make_app_state()

    mock_orchestrator = MagicMock()
    mock_orchestrator.course_name = "NET101"

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._orchestrator = mock_orchestrator

            await pilot.click("#submit-grades")

            submit_status = app.screen.query_one("#submit-status", Static)
            rendered_text = str(submit_status.render())
            assert "No preview rows" in rendered_text


@pytest.mark.asyncio
async def test_gradeliner_zero_ungraded_cancelled_writes_nothing():
    """Verify cancelling Zero Ungraded ConfirmModal writes nothing to Canvas."""
    state = _make_app_state()

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._selected_assignment_ids = {1001}

            screen.action_zero_ungraded()
            await wait_for_confirm_modal(app, pilot)
            await pilot.pause()
            await pilot.click("#confirm-no")
            await wait_for_gradeliner_screen(app, pilot)

            state.canvas_client.get_assignment_submissions.assert_not_called()
            state.canvas_client.submit_grade.assert_not_called()
            state.canvas_client.zero_out_ungraded_past_due.assert_not_called()


@pytest.mark.asyncio
async def test_gradeliner_zero_ungraded_confirmed_uses_safe_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify confirming Zero Ungraded writes a backup first and submits via SafeCanvasWriter."""
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    state = _make_app_state()
    state.canvas_client.get_assignment_submissions.return_value = [
        SimpleNamespace(user_id=101, score=None, excused=False),
        SimpleNamespace(user_id=102, score=85.0, excused=False),
    ]
    state.canvas_client.get_submission.return_value = {"user_id": 101, "score": None}
    state.canvas_client.submit_grade.return_value = True

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._selected_assignment_ids = {1001}

            screen.action_zero_ungraded()
            await wait_for_confirm_modal(app, pilot)
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await wait_for_gradeliner_screen(app, pilot)

            state.canvas_client.zero_out_ungraded_past_due.assert_not_called()
            state.canvas_client.get_assignment_submissions.assert_called_once_with(42, 1001)
            state.canvas_client.get_submission.assert_called_once_with(42, 1001, 101)
            state.canvas_client.submit_grade.assert_called_once_with(42, 1001, 101, 0, None)
            backups = list(tmp_path.rglob("*.json"))
            assert len(backups) == 1


@pytest.mark.asyncio
async def test_gradeliner_zero_ungraded_dry_run_skips_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify dry-run mode (execute=False) writes neither grades nor backups."""
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    state = _make_app_state()
    state.canvas_client.get_assignment_submissions.return_value = [
        SimpleNamespace(user_id=101, score=None, excused=False),
    ]

    with patch(
        "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
        return_value=None,
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._selected_assignment_ids = {1001}

            screen._zero_ungraded_worker(execute=False)
            await pilot.pause(0.1)
            await wait_for_gradeliner_screen(app, pilot)

            state.canvas_client.submit_grade.assert_not_called()
            assert list(tmp_path.rglob("*.json")) == []


@pytest.mark.asyncio
async def test_gradeliner_zero_ungraded_failure_shows_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify Zero Ungraded surfaces safe-write errors in TransmissionModal and notifies."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    state = _make_app_state()

    with (
        patch(
            "coursewright.ui.tui.screens.gradeliner._find_newest_netacad_csv",
            return_value=None,
        ),
        patch(
            "coursewright.core.canvas.safe_write.resolve_workspace_directory",
            return_value=None,
        ),
    ):
        app = GradeLinerTestApp(state)
        async with app.run_test() as pilot:
            screen = app.screen
            screen._selected_assignment_ids = {1001}

            with patch.object(app, "notify") as mock_notify:
                screen.action_zero_ungraded()
                await wait_for_confirm_modal(app, pilot)
                await pilot.pause()
                await pilot.click("#confirm-yes")

                for _attempt_index in range(40):
                    if isinstance(app.screen, TransmissionModal) and app.screen._has_error:
                        break
                    await pilot.pause(0.05)

                assert isinstance(app.screen, TransmissionModal)
                assert app.screen._has_error is True
                error_calls = [
                    call
                    for call in mock_notify.call_args_list
                    if call.kwargs.get("severity") == "error"
                ]
                assert error_calls
                state.canvas_client.submit_grade.assert_not_called()
