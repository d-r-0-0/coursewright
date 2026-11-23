"""Textual pilot tests for the independent CSV workflow, with mocked Canvas."""

import json
import os
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from textual.app import App
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Input,
    RichLog,
    Select,
    SelectionList,
    Static,
    TabbedContent,
)

from coursewright.core.inbox import discovery as inbox_discovery
from coursewright.core.traffic import TrafficEvent, publish, publish_chunks
from coursewright.core.traffic import bus as traffic_bus
from coursewright.ta.zybooks.importer import MappingCache, read_snapshot
from coursewright.ui.tui.screens.zybooks import ZyBooksScreen, describe_send_confirmation
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal
from tests.ta.zybooks.test_zybooks_importer import (
    FakeCanvas,
    ai_mapping_response,
    write_csv,
    write_shared_csv,
)


class ImportApp(App):
    def __init__(self, state, directory=None, cache=None):
        super().__init__()
        self.app_state = state
        self.directory = directory
        self.cache = cache

    def on_mount(self):
        self.push_screen(ZyBooksScreen(self.app_state, self.directory, self.cache))


async def wait_idle(screen, pilot):
    await screen.workers.wait_for_complete()
    await pilot.pause()


async def wait_until(pilot, predicate, tries=20):
    for _ in range(tries):
        if predicate():
            return True
        await pilot.pause()
    return predicate()


async def click_tab(pilot, screen, selector):
    """Closing a dropdown can re-focus and scroll later, so confirm the tab is on screen."""
    tab = screen.query_one(selector)
    for _ in range(20):
        screen.set_focus(None)
        screen.query_one("#zybooks-body").scroll_home(animate=False)
        await pilot.pause()
        if tab.region.y >= 0 and tab.region.bottom <= screen.size.height:
            break
    return await pilot.click(selector)


async def click_widget(pilot, screen, selector, offset=(2, 1)):
    """Textual may scroll again after focus settles, so confirm visibility before clicking."""
    widget = screen.query_one(selector)
    for _ in range(20):
        widget.scroll_visible(animate=False)
        await pilot.pause()
        if widget.region.y >= 0 and widget.region.bottom <= screen.size.height:
            break
    return await pilot.click(selector, offset=offset)


@pytest.mark.asyncio
async def test_downloads_first_ordering_suggests_newest_matching_report_without_importing(
    tmp_path, monkeypatch
):
    downloads = tmp_path / "Downloads"
    workspace_import = tmp_path / "workspace" / "import"
    downloads.mkdir(parents=True)
    workspace_import.mkdir(parents=True)

    older_download = write_csv(downloads / "Example_report_old.csv")
    newer_download = write_csv(downloads / "Example_report_new.csv")
    non_matching_download = downloads / "unrelated_export.csv"
    non_matching_download.write_text("not,a,zybooks,report\n", encoding="utf-8")
    newest_workspace = write_csv(workspace_import / "Workspace_report_newest.csv")

    os.utime(older_download, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer_download, ns=(2_000_000_000, 2_000_000_000))
    os.utime(non_matching_download, ns=(3_000_000_000, 3_000_000_000))
    os.utime(newest_workspace, ns=(4_000_000_000, 4_000_000_000))

    monkeypatch.setattr(inbox_discovery, "default_download_directory", lambda: downloads)
    monkeypatch.setattr(
        inbox_discovery, "default_workspace_import_directory", lambda: workspace_import
    )

    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="ABC 101", course_code="ABC101")
    state.canvas_client = FakeCanvas()
    app = ImportApp(state, directory=None, cache=MappingCache(tmp_path / "cache"))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#file-path", Input).value == str(newer_download.resolve())
        file_info = str(screen.query_one("#file-info", Static).render())
        assert "Example_report_new.csv" in file_info
        assert "Downloads" in file_info
        assert "not yet imported" in file_info
        assert "Downloads" in str(screen.query_one("#wizard-status", Static).render())
        # Suggestion alone must never read or import the CSV or write to Canvas.
        assert screen.snapshot is None
        assert screen.coordinator is None
        assert screen.plan is None
        assert not state.canvas_client.writes
        with pytest.raises(ValueError, match="Inspect the selected file first"):
            screen._load_mappings()


@pytest.mark.asyncio
async def test_falls_back_to_workspace_import_when_downloads_has_no_matching_report(
    tmp_path, monkeypatch
):
    downloads = tmp_path / "Downloads"
    workspace_import = tmp_path / "workspace" / "import"
    downloads.mkdir(parents=True)
    workspace_import.mkdir(parents=True)

    (downloads / "other_grades.csv").write_text("col1,col2\n", encoding="utf-8")
    workspace_report = write_csv(workspace_import / "Workspace_report_1.csv")

    monkeypatch.setattr(inbox_discovery, "default_download_directory", lambda: downloads)
    monkeypatch.setattr(
        inbox_discovery, "default_workspace_import_directory", lambda: workspace_import
    )

    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="ABC 101", course_code="ABC101")
    state.canvas_client = FakeCanvas()
    app = ImportApp(state, directory=None, cache=MappingCache(tmp_path / "cache"))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#file-path", Input).value == str(workspace_report.resolve())
        assert "workspace import" in str(screen.query_one("#file-info", Static).render())
        assert screen.snapshot is None
        assert not state.canvas_client.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (140, 50)])
async def test_file_to_preview_without_browser_or_writes(tmp_path, size):
    path = write_csv(tmp_path / "Example_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="ABC 101", course_code="ABC101")
    state.canvas_client = FakeCanvas()
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#file-path", Input).value == str(path.resolve())
        screen._inspect_file()
        await wait_idle(screen, pilot)
        assert screen.snapshot
        assert "Book candidate: Example" in str(screen.query_one("#file-info", Static).render())
        screen.query_one("#pair-confirm", Checkbox).value = True
        screen._load_mappings()
        await wait_idle(screen, pilot)
        assert screen.step == 2
        # The step's primary action must stay on screen without scrolling the body.
        approve = screen.query_one("#approve-mappings", Button)
        assert approve.display and approve.region.y >= 0
        assert approve.region.bottom <= size[1]
        assert not screen.query_one("#build-preview", Button).display
        screen.query_one("#distinct-labs", Checkbox).value = True
        screen._approve_mappings()
        await pilot.pause()
        assert screen.step == 3
        screen._build_preview()
        await wait_idle(screen, pilot)
        assert screen.step == 4
        assert {str(row.new_points) for row in screen.preview.rows} == {"35.00", "40.00"}
        assert not state.canvas_client.writes
        assert screen.query_one("#row-selection", SelectionList).selected == []
        await pilot.pause()
        screen.query_one("#send-rows").scroll_visible(animate=False)
        await pilot.pause()
        assert screen.query_one("#send-rows").region.bottom <= size[1]


@pytest.mark.asyncio
async def test_pairing_and_mapping_corrections_are_explicit(tmp_path):
    write_csv(tmp_path / "Example_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Course", course_code="C")
    state.canvas_client = FakeCanvas()
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    async with app.run_test() as pilot:
        screen = app.screen
        screen._inspect_file()
        await wait_idle(screen, pilot)
        with pytest.raises(ValueError, match="association"):
            screen._load_mappings()
        screen.query_one("#pair-confirm", Checkbox).value = True
        screen._load_mappings()
        await wait_idle(screen, pilot)
        screen.query_one("#source-student", Select).value = "example@example.invalid"
        await pilot.pause()
        screen.query_one("#canvas-student", Select).value = 0
        screen._save_student()
        assert screen.plan.students[0].canvas_id is None
        assert not screen.plan.approved


async def open_preview(screen, pilot, include_existing=False):
    """Drive the wizard from CSV inspection to the step-4 preview (no writes)."""
    screen._inspect_file()
    await wait_idle(screen, pilot)
    screen.query_one("#pair-confirm", Checkbox).value = True
    screen._load_mappings()
    await wait_idle(screen, pilot)
    screen.query_one("#distinct-labs", Checkbox).value = True
    screen._approve_mappings()
    await pilot.pause()
    if include_existing:
        screen.query_one("#include-existing", Checkbox).value = True
        await pilot.pause()
    screen._build_preview()
    await wait_idle(screen, pilot)
    assert screen.step == 4


def spy_on_coordinator(screen):
    """Wrap approve_rows/submit so tests can count calls without changing behavior."""
    coordinator = screen.coordinator
    coordinator.approve_rows = Mock(wraps=coordinator.approve_rows)
    coordinator.submit = Mock(wraps=coordinator.submit)
    return coordinator


@pytest.mark.asyncio
async def test_single_confirmation_sends_selected_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path / "ws"))
    write_csv(tmp_path / "Example_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Course", course_code="C")
    state.canvas_client = FakeCanvas()
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        await open_preview(screen, pilot)
        # The old three-gate flow is gone: no risky checkbox, approve or submit button.
        for removed in ("#risky-confirm", "#approve-rows", "#submit-grades"):
            assert not screen.query(removed)
        coordinator = spy_on_coordinator(screen)
        screen.query_one("#row-selection", SelectionList).select_all()
        await pilot.pause()
        await click_widget(pilot, screen, "#send-rows", offset=(2, 0))
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ConfirmModal)
        assert "Send 2 grade rows to Canvas for Course?" in modal._prompt_text
        assert "2 new (ungraded now) · 0 raised · 0 zero/decrease" in modal._prompt_text
        assert "rechecked right before sending" in modal._prompt_text
        assert "No automatic retries" in modal._prompt_text
        assert "⚠" not in modal._prompt_text
        assert modal._yes_label == "Send 2 rows"
        # The modal alone must not approve or write anything.
        assert coordinator.approval is None
        assert not state.canvas_client.writes
        await pilot.click("#confirm-yes")
        await wait_idle(screen, pilot)
        assert screen.step == 5
        coordinator.approve_rows.assert_called_once()
        assert coordinator.approve_rows.call_args.kwargs["accept_risky"] is False
        coordinator.submit.assert_called_once()
        assert len(state.canvas_client.writes) == 2
        assert coordinator.approval is None  # consumed by submit, single-use
        assert not screen.query_one("#send-rows", Button).display
        transcript = "\n".join(
            strip.text for strip in screen.query_one("#submission-log", RichLog).lines
        )
        assert "Row 1/2: Student Example" in transcript
        assert "40.00" in transcript
        assert "read back as Verified" in transcript
        assert "Submission results" in transcript
        # The single-use guard refuses a second post from the same approval.
        with pytest.raises(ValueError, match="already used"):
            screen._submit()
        assert len(state.canvas_client.writes) == 2


@pytest.mark.asyncio
async def test_cancelling_send_confirmation_posts_nothing(tmp_path):
    write_csv(tmp_path / "Example_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Course", course_code="C")
    state.canvas_client = FakeCanvas()
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        await open_preview(screen, pilot)
        coordinator = spy_on_coordinator(screen)
        screen.query_one("#row-selection", SelectionList).select_all()
        await pilot.pause()
        screen._confirm_and_send_rows()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.click("#confirm-no")
        await wait_idle(screen, pilot)
        assert app.screen is screen
        assert screen.step == 4
        assert coordinator.approval is None
        coordinator.approve_rows.assert_not_called()
        coordinator.submit.assert_not_called()
        assert not state.canvas_client.writes
        assert "Nothing was sent" in str(screen.query_one("#wizard-status", Static).render())


@pytest.mark.asyncio
async def test_send_without_selection_explains_and_opens_no_modal(tmp_path):
    write_csv(tmp_path / "Example_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Course", course_code="C")
    state.canvas_client = FakeCanvas()
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        await open_preview(screen, pilot)
        await click_widget(pilot, screen, "#send-rows", offset=(2, 0))
        await pilot.pause()
        assert app.screen is screen
        assert "Select at least one" in str(screen.query_one("#wizard-status", Static).render())
        assert not state.canvas_client.writes


@pytest.mark.asyncio
async def test_zeros_and_decreases_are_named_in_the_single_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path / "ws"))
    write_csv(tmp_path / "Example_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Course", course_code="C")
    state.canvas_client = FakeCanvas()
    # Canvas already holds 45 on both targets, so the CSV's 35 and 40 are decreases.
    for record in state.canvas_client.records:
        record.update(score=45, workflow_state="graded", grade="45")
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        await open_preview(screen, pilot, include_existing=True)
        assert all(row.risky for row in screen.preview.rows)
        coordinator = spy_on_coordinator(screen)
        screen.query_one("#row-selection", SelectionList).select_all()
        await pilot.pause()
        screen._confirm_and_send_rows()
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ConfirmModal)
        assert "0 new (ungraded now) · 0 raised · 2 zero/decrease" in modal._prompt_text
        assert "⚠ 2 selected rows are a zero or lower" in modal._prompt_text
        assert modal._yes_label == "Send, including 2 zeros/decreases"
        assert coordinator.approval is None
        assert not state.canvas_client.writes
        await pilot.click("#confirm-yes")
        await wait_idle(screen, pilot)
        coordinator.approve_rows.assert_called_once()
        assert coordinator.approve_rows.call_args.kwargs["accept_risky"] is True
        coordinator.submit.assert_called_once()
        assert sorted(write[3] for write in state.canvas_client.writes) == [35.0, 40.0]


def test_send_confirmation_text_counts_each_kind_of_change():
    def row(old, new):
        return SimpleNamespace(
            old_points=None if old is None else Decimal(old),
            new_points=Decimal(new),
            risky=Decimal(new) == 0 or old is not None and Decimal(new) < Decimal(old),
        )

    rows = [row(None, "10"), row(None, "0"), row("5", "8"), row("9", "7")]
    prompt, label = describe_send_confirmation(rows, "Intro [lab] 101")
    # Course names are escaped so bracketed text cannot act as Rich markup.
    assert "Send 4 grade rows to Canvas for Intro \\[lab] 101?" in prompt
    assert "1 new (ungraded now) · 1 raised · 2 zero/decrease" in prompt
    assert label == "Send, including 2 zeros/decreases"
    single_prompt, single_label = describe_send_confirmation([row(None, "10")], "C")
    assert "Send 1 grade row to Canvas for C?" in single_prompt
    assert single_label == "Send 1 row"


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (140, 50)])
async def test_sorted_bounded_targets_and_shared_csv_student_filter(tmp_path, size):
    write_shared_csv(tmp_path / "SharedBook_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="ABC101 Class A", course_code="ABC101")
    state.canvas_client = FakeCanvas()
    state.canvas_client.assignments[0].update(
        name="U01A01: zyBook Participation & Challenge",
        points_possible=100,
        description="Chapter 2",
    )
    state.canvas_client.assignments[1].update(
        name="U01L01: zyBook Labs",
        description="Chapter 2",
    )
    state.canvas_client.assignments.reverse()
    state.canvas_client.assignments[:0] = [
        {
            "id": 200 + unit,
            "name": f"U{unit:02}A01: zyBook Participation & Challenge",
            "points_possible": 50,
        }
        for unit in range(13, 1, -1)
    ]
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    async with app.run_test(size=size) as pilot:
        screen = app.screen
        screen._inspect_file()
        await wait_idle(screen, pilot)
        assert await click_widget(pilot, screen, "#pair-confirm")
        await pilot.pause()
        assert screen.query_one("#pair-confirm", Checkbox).value
        assert await click_widget(pilot, screen, "#load-mappings")
        await wait_idle(screen, pilot)
        assert screen.step == 2
        assert screen.query_one("#mapping-tabs", TabbedContent).active == "unit-mappings"
        assert "1/2 CSV students matched; 1 skipped" in str(
            screen.query_one("#student-coverage", Static).render()
        )
        for identifier in ("activity-target", "lab-target"):
            target = screen.query_one(f"#{identifier}", Select)
            target.focus()
            await pilot.pause()
            target.scroll_visible(animate=False)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert target.expanded
            overlay = target.query_one("SelectOverlay")
            labels = [
                overlay.get_option_at_index(index).prompt.plain
                for index in range(1, overlay.option_count)
            ]
            assert labels == sorted(labels, key=str.casefold)
            assert overlay.region.height <= 8
            assert 0 <= overlay.region.y < overlay.region.bottom <= size[1]
            assert 0 <= overlay.region.x < overlay.region.right <= size[0]
            await pilot.press("escape")
            await pilot.pause()
            assert screen.step == 2
        assert screen.query_one("#student-table", DataTable).row_count == 1
        assert await click_tab(pilot, screen, "#--content-tab-student-mappings")
        await pilot.pause()
        assert screen.query_one("#mapping-tabs", TabbedContent).active == "student-mappings"
        assert await click_widget(pilot, screen, "#show-unmatched")
        await pilot.pause()
        assert screen.query_one("#show-unmatched", Checkbox).value
        assert screen.query_one("#student-table", DataTable).row_count == 2
        screen.query_one("#source-student", Select).value = "other@example.invalid"
        await pilot.pause()
        assert screen.query_one("#canvas-student", Select).value == 0
        assert await click_tab(pilot, screen, "#--content-tab-unit-mappings")
        await pilot.pause()
        screen.query_one("#distinct-labs", Checkbox).value = True
        screen._approve_mappings()
        await pilot.pause()
        screen._build_preview()
        await wait_idle(screen, pilot)
        assert screen.step == 4
        assert {row.new_points for row in screen.preview.rows} == {70, 40}
        assert screen.preview.unmatched_source == 1
        assert not state.canvas_client.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (140, 50)])
async def test_canvas_then_ai_populates_units_without_typing_and_reuses_approval(tmp_path, size):
    path = write_csv(tmp_path / "Example_report_1.csv")
    snapshot = read_snapshot(path)
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Synthetic course")
    state.canvas_client = FakeCanvas()
    state.canvas_client.assignments[0]["name"] = "U01A01: zyBook Participation & Challenge"
    state.canvas_client.assignments[0]["description"] = "Complete chapter 2."
    state.canvas_client.assignments[0]["points_possible"] = 100
    state.canvas_client.assignments[1]["name"] = "U01A02: zyLabs"
    state.canvas_client.assignments[1]["description"] = "Complete chapter 2 labs."
    state.llm_client = Mock()

    def respond(**kwargs):
        payload = json.loads(kwargs["prompt"])
        assert payload["assignments"][0]["name"] == state.canvas_client.assignments[0]["name"]
        return SimpleNamespace(content=json.dumps(ai_mapping_response(snapshot.sections)))

    state.llm_client.generate_sync.side_effect = respond
    cache = MappingCache(tmp_path / "cache")
    app = ImportApp(state, tmp_path, cache)
    async with app.run_test(size=size) as pilot:
        screen = app.screen
        screen._inspect_file()
        await wait_idle(screen, pilot)
        screen.query_one("#pair-confirm", Checkbox).value = True
        screen._load_mappings()
        await wait_idle(screen, pilot)
        assert state.llm_client.generate_sync.call_count == 1
        assert screen.step == 2 and not screen.plan.approved
        assert len(screen.plan.units) == 2
        assert (
            screen.query_one("#unit-name", Input).value
            == state.canvas_client.assignments[0]["name"]
        )
        assert "Chapter 2" in str(screen.query_one("#mapping-notes", Static).render())
        assert not cache.path(42, "Example").exists()
        assert not screen.query_one("#lab-review").display
        screen._approve_mappings()
        await pilot.pause()
        assert screen.step == 2
        assert screen.query_one("#unit-choice", Select).value == 2
        assert screen.query_one("#lab-review").display
        assert len(screen.query_one("#unit-labs", SelectionList).selected) == 7
        assert "7 of 7 eligible labs checked" in str(
            screen.query_one("#lab-summary", Static).render()
        )
        assert "confirm each checked section is one separate lab" in str(
            screen.query_one("#wizard-status", Static).render()
        )
        screen.query_one("#distinct-labs", Checkbox).value = True
        screen.query_one("#unit-labs", SelectionList).deselect("2.18")
        assert await wait_until(
            pilot, lambda: not screen.query_one("#distinct-labs", Checkbox).value
        )
        screen.query_one("#unit-labs", SelectionList).select("2.18")
        await pilot.pause()
        assert await click_widget(pilot, screen, "#distinct-labs")
        await pilot.pause()
        assert screen.query_one("#distinct-labs", Checkbox).value
        assert await click_widget(pilot, screen, "#approve-mappings")
        await pilot.pause()
        assert screen.step == 3
        choices = screen.query_one("#assignment-selection", SelectionList)
        labels = [
            choices.get_option_at_index(index).prompt.plain for index in range(choices.option_count)
        ]
        assert labels == [
            "U01A01: zyBook Participation & Challenge  <-  "
            "CSV participation + challenges, sections 2.1, 2.12-2.18",
            "U01A02: zyLabs  <-  CSV labs 2.12-2.18, best 5 per student",
        ]
        screen._build_preview()
        await wait_idle(screen, pilot)
        assert {row.new_points for row in screen.preview.rows} == {70, 40}
        assert not state.canvas_client.writes
        screen._show_step(1)
        await pilot.pause()
        screen.query_one("#pair-confirm", Checkbox).value = True
        screen._load_mappings()
        await wait_idle(screen, pilot)
        assert screen.plan.approved
        assert state.llm_client.generate_sync.call_count == 1


@pytest.mark.asyncio
async def test_lab_target_without_lab_columns_is_explained_and_recoverable(tmp_path):
    write_csv(tmp_path / "Example_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Synthetic course")
    state.canvas_client = FakeCanvas()
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    async with app.run_test(size=(140, 50)) as pilot:
        screen = app.screen
        screen._inspect_file()
        await wait_idle(screen, pilot)
        screen.query_one("#pair-confirm", Checkbox).value = True
        screen._load_mappings()
        await wait_idle(screen, pilot)
        screen.query_one("#unit-sections", Input).value = "2.1"
        await pilot.pause()
        screen.query_one("#lab-target", Select).value = 102
        await pilot.pause()
        assert await wait_until(
            pilot, lambda: not screen.query_one("#unit-labs", SelectionList).display
        )
        assert "no lab columns" in str(screen.query_one("#lab-summary", Static).render()).lower()
        screen._approve_mappings()
        await pilot.pause()
        assert screen.step == 2
        assert screen.query_one("#unit-table", DataTable).get_row_at(0)[1] == "No lab columns"
        assert screen.app.focused.id == "lab-target"
        assert "contain no lab columns" in str(screen.query_one("#wizard-status", Static).render())
        screen.query_one("#unit-sections", Input).value = "2.1,2.12-2.18"
        assert await wait_until(
            pilot, lambda: screen.query_one("#unit-labs", SelectionList).display
        )
        screen._approve_mappings()
        await pilot.pause()
        assert screen.step == 2
        assert screen.query_one("#unit-table", DataTable).get_row_at(0)[1] == "Check 5 labs"
        assert screen.app.focused.id == "unit-labs"
        screen.query_one("#unit-labs", SelectionList).select_all()
        await pilot.pause()
        screen.query_one("#distinct-labs", Checkbox).value = True
        screen._approve_mappings()
        await pilot.pause()
        assert screen.step == 3
        assert not state.canvas_client.writes


@pytest.mark.asyncio
async def test_ai_failure_leaves_mapping_screen_usable(tmp_path):
    write_csv(tmp_path / "Example_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Synthetic course")
    state.canvas_client = FakeCanvas()
    state.llm_client = Mock()
    state.llm_client.generate_sync.side_effect = RuntimeError("private error")
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    async with app.run_test() as pilot:
        screen = app.screen
        screen._inspect_file()
        await wait_idle(screen, pilot)
        screen.query_one("#pair-confirm", Checkbox).value = True
        screen._load_mappings()
        await wait_idle(screen, pilot)
        assert screen.step == 2 and screen.plan.units
        assert "request failed" in str(screen.query_one("#mapping-notes", Static).render())
        assert isinstance(app.screen, TransmissionModal)
        assert app.screen._has_error
        await pilot.press("escape")
        assert app.screen is screen
        assert not screen.query_one("#suggest-mappings").disabled
        assert not state.canvas_client.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("outcome", ["success", "fenced-json", "invalid-json", "request-error"])
async def test_mapping_modal_captures_traffic_and_retains_failures(tmp_path, refresh, outcome):
    write_csv(tmp_path / "Example_report_1.csv")
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Synthetic course")
    state.canvas_client = FakeCanvas()
    app = ImportApp(state, tmp_path, MappingCache(tmp_path / "cache"))
    observed_modals = []

    def roster_with_traffic(course_id):
        modal = app.screen
        assert isinstance(modal, TransmissionModal)
        observed_modals.append(modal)
        publish(TrafficEvent("canvas", "GET", "courses/42/users", 0, 0))
        return state.canvas_client.roster

    state.canvas_client.get_course_users = roster_with_traffic

    def respond(**kwargs):
        modal = app.screen
        assert isinstance(modal, TransmissionModal)
        observed_modals.append(modal)
        publish_chunks("llm", "test-model", "chat.completions", "out", kwargs["prompt"])
        publish(TrafficEvent("llm", "test-model", "chat.completions", 0, 0))
        if outcome == "request-error":
            publish(TrafficEvent("llm", "test-model", "chat.completions", -1, 10))
            raise RuntimeError("Synthetic provider failure")
        content = (
            json.dumps(ai_mapping_response(screen.snapshot.sections))
            if outcome in {"success", "fenced-json"}
            else "[not valid mapping JSON]"
        )
        if outcome == "fenced-json":
            content = f"```json\n{content}\n```"
        publish(TrafficEvent("llm", "test-model", "chat.completions", 200, 10))
        publish_chunks("llm", "test-model", "chat.completions", "in", content)
        return SimpleNamespace(content=content)

    model = Mock()
    model.generate_sync.side_effect = respond
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        screen._inspect_file()
        await wait_idle(screen, pilot)
        state.llm_client = None if refresh else model
        screen.query_one("#pair-confirm", Checkbox).value = True
        screen._load_mappings()
        await wait_idle(screen, pilot)
        assert screen.step == 2
        assert "courses/42/users" in "\n".join(observed_modals[0]._log_lines)
        original_plan = screen.plan
        if refresh:
            state.llm_client = model
            screen._suggest_mappings()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            await pilot.click("#confirm-yes")
            await wait_idle(screen, pilot)
        modal = observed_modals[-1]
        transcript = "\n".join(modal._log_lines)
        assert "test-model" in transcript and "requesting" in transcript
        assert "columns" not in transcript  # private prompt bodies are never echoed
        model.generate_sync.assert_called_once()
        assert not screen._busy
        if outcome in {"success", "fenced-json"}:
            assert app.screen is screen
            assert modal not in app.screen_stack
            assert not modal._has_error
            assert await wait_until(
                pilot, lambda: modal._traffic_callback not in traffic_bus._traffic_subscribers
            )
            assert len(screen.plan.units) == 2
        else:
            assert app.screen is modal and modal._has_error
            expected_error = (
                "invalid mapping JSON" if outcome == "invalid-json" else "request failed"
            )
            assert expected_error in str(modal.query_one("#transmission-status", Static).render())
            assert expected_error in str(screen.query_one("#mapping-notes", Static).render())
            assert not screen.query_one(Collapsible).collapsed
            if outcome == "invalid-json":
                assert "not valid mapping JSON" not in transcript
            else:
                assert "ERROR" in transcript
            if refresh:
                assert screen.plan is original_plan
            await pilot.press("escape")
            assert app.screen is screen
            assert not screen.query_one("#suggest-mappings", Button).disabled
            assert modal._traffic_callback not in traffic_bus._traffic_subscribers
        assert not state.canvas_client.writes


@pytest.mark.asyncio
async def test_unapproved_proposal_reopens_from_cache_without_model(tmp_path):
    path = write_csv(tmp_path / "Example_report_1.csv")
    snapshot = read_snapshot(path)
    state = AppState()
    state.current_course = SimpleNamespace(id=42, name="Synthetic course")
    state.canvas_client = FakeCanvas()
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content=json.dumps(ai_mapping_response(snapshot.sections))
    )
    cache = MappingCache(tmp_path / "cache")
    for model in (llm, None):
        state.llm_client = model
        app = ImportApp(state, tmp_path, cache)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            screen._inspect_file()
            await wait_idle(screen, pilot)
            screen.query_one("#pair-confirm", Checkbox).value = True
            screen._load_mappings()
            await wait_idle(screen, pilot)
            assert screen.step == 2 and len(screen.plan.units) == 2
            assert not screen.plan.approved
            assert not any(unit.distinct_labs_confirmed for unit in screen.plan.units)
            if model is None:
                assert "Cached AI proposal" in str(
                    screen.query_one("#mapping-notes", Static).render()
                )
    assert llm.generate_sync.call_count == 1
    assert not cache.path(42, "Example").exists()
    assert not state.canvas_client.writes


async def check_private_csv_preview(path: Path) -> dict:
    """Opt-in local acceptance check; never a committed fixture or network call.

    The caller supplies an existing private CSV. Only aggregate results escape
    this function. Canvas identities and grade reads are mocked in memory, and
    the mapping cache is placed in an automatically removed temporary directory.
    """
    import csv
    import re
    from decimal import ROUND_HALF_UP

    from coursewright.core.canvas.models import Assignment
    from tests.ui.tui.test_course_list_screen import (
        CourseListTestApp,
        create_mock_course,
        wait_for_course_list_screen,
    )

    snapshot = read_snapshot(path)
    canvas = FakeCanvas()
    canvas.assignments = [Assignment(**assignment) for assignment in canvas.assignments]
    canvas.roster = [
        {"id": index, "name": student.name, "email": student.email}
        for index, student in enumerate(snapshot.students, 1)
    ]
    canvas.records = [
        {
            "assignment_id": target,
            "user_id": student["id"],
            "score": None,
            "excused": False,
            "workflow_state": "unsubmitted",
        }
        for student in canvas.roster
        for target in (101, 102)
    ]
    state = AppState()
    state.current_course = create_mock_course(42, "CSV validation — mock Canvas")
    state.filtered_courses = [state.current_course]
    state.canvas_client = canvas
    with TemporaryDirectory(prefix="zybooks-preview-") as temporary:
        app = CourseListTestApp(state)
        async with app.run_test(size=(80, 24)) as pilot:
            await wait_for_course_list_screen(app, pilot)
            await pilot.pause()
            await wait_for_course_list_screen(app, pilot)
            await pilot.press("y")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ZyBooksScreen)
            screen.cache = MappingCache(Path(temporary))
            screen.query_one("#file-path", Input).value = str(path.resolve())
            screen._inspect_file()
            await wait_idle(screen, pilot)
            screen.query_one("#pair-confirm", Checkbox).value = True
            screen._load_mappings()
            await wait_idle(screen, pilot)
            screen.query_one("#distinct-labs", Checkbox).value = True
            screen._approve_mappings()
            await pilot.pause()
            screen._build_preview()
            await wait_idle(screen, pilot)
            assert screen.step == 4 and screen.preview is not None
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            metadata = []
            for index, header in enumerate(rows[0]):
                match = re.fullmatch(r"2\.\d+ - (Participation|Challenge|Lab) \((.+)\)", header)
                if match and Decimal(match[2]) > 0:
                    metadata.append((index, match[1], Decimal(match[2])))
            actual = {
                (row.student_id, row.assignment_id): row.new_points for row in screen.preview.rows
            }
            comparisons = 0
            for index, source in enumerate(rows[1:], 1):
                labs = sorted(
                    (
                        Decimal(source[column])
                        for column, category, _ in metadata
                        if category == "Lab"
                    ),
                    reverse=True,
                )
                lab_points = sum(labs[:5]) / 5 / 100 * 50
                activity_points = Decimal(0)
                for category in ("Participation", "Challenge"):
                    cells = [
                        (Decimal(source[column]), weight)
                        for column, label, weight in metadata
                        if label == category
                    ]
                    activity_points += (
                        sum(score * weight for score, weight in cells)
                        / sum(weight for _, weight in cells)
                        / 100
                        * 25
                    )
                for target, points in ((101, activity_points), (102, lab_points)):
                    assert actual[index, target] == points.quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    comparisons += 1
            assert not canvas.writes
            return {
                "students": len(snapshot.students),
                "preview_rows": len(screen.preview.rows),
                "independent_calculations_matched": comparisons,
                "canvas_writes": len(canvas.writes),
                "terminal": "80x24",
                "route": "Tools > zyBooks",
            }
