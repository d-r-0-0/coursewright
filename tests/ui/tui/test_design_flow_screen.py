"""Course-design flow: opening it and previewing each of the five phases.

All Canvas access goes through ``FakeDesignCanvas`` (no network).  Every
preview test also proves the preview wrote nothing and left no backup.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Input

from coursewright.core import traffic
from coursewright.core.canvas.models import CanvasCourse
from coursewright.ui.tui.screens.course_list import (
    TOOL_SCREEN_LOCATIONS,
    CourseListScreen,
    ShortcutLink,
    find_tool_screen_class,
)
from coursewright.ui.tui.screens.design_flow import CourseDesignScreen
from coursewright.ui.tui.screens.design_phases import DESIGN_PHASES
from coursewright.ui.tui.state import AppState

from .activity_helpers import OneScreenApp, make_course_dict, wait_until
from .design_fakes import (
    COURSE_NAME,
    SCHEDULE_TEXT,
    FakeDesignCanvas,
    backup_files,
    make_design_app,
    preview_phase,
    table_text,
    wait_for_design_screen,
)


@pytest.fixture(autouse=True)
def clean_traffic_bus():
    traffic.reset_for_tests()
    yield
    traffic.reset_for_tests()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "workspace"
    directory.mkdir()
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(directory))
    return directory


# -- opening the flow from the course list ---------------------------------


def test_course_design_is_registered_as_a_tool_screen() -> None:
    assert "course design" in TOOL_SCREEN_LOCATIONS
    assert find_tool_screen_class("course design") is CourseDesignScreen


def course_list_app() -> OneScreenApp:
    state = AppState()
    state.filtered_courses = [CanvasCourse.model_validate(make_course_dict(7))]
    return OneScreenApp(CourseListScreen(state), state)


@pytest.mark.asyncio
async def test_w_key_opens_design_flow_for_the_highlighted_course() -> None:
    app = course_list_app()
    async with app.run_test() as pilot:
        await wait_until(pilot, lambda: isinstance(app.screen, CourseListScreen), "course list")
        await pilot.press("w")
        screen = await wait_for_design_screen(app, pilot)
        assert screen.course_id == 7
        assert "Sample Course" in str(screen.query_one("#design-title").render())


@pytest.mark.asyncio
async def test_sidebar_shortcut_opens_design_flow() -> None:
    app = course_list_app()
    async with app.run_test() as pilot:
        await wait_until(pilot, lambda: isinstance(app.screen, CourseListScreen), "course list")
        link = app.screen.query_one("#shortcut-open-course-design", ShortcutLink)
        assert "Course design" in str(link.render())
        app.screen.on_shortcut_link_clicked(ShortcutLink.Clicked(link))
        await wait_for_design_screen(app, pilot)


# -- moving between phases -------------------------------------------------


@pytest.mark.asyncio
async def test_phase_keys_buttons_and_next_previous(workspace: Path) -> None:
    app = make_design_app(FakeDesignCanvas())
    async with app.run_test() as pilot:
        screen = await wait_for_design_screen(app, pilot)
        assert screen.current_phase.key == "diagnose"
        assert not screen.query_one("#apply-button", Button).display  # read-only
        await pilot.press("n")
        assert screen.current_phase.key == "organize"
        assert screen.query_one("#apply-button", Button).display
        await pilot.press("4")
        assert screen.current_phase.key == "dates"
        assert screen.query_one("#schedule-file", Input).display
        await pilot.press("b")
        assert screen.current_phase.key == "discussions"
        await pilot.click("#phase-publish")
        assert screen.current_phase.key == "publish"
        assert screen.query_one("#phase-publish", Button).has_class("current-phase")
        await pilot.press("n")  # wraps round to the first phase
        assert screen.current_phase.key == "diagnose"
        description = str(screen.query_one("#phase-description").render())
        assert "Phase 1 of 5" in description and "read-only" in description


def test_there_are_exactly_five_phases_in_order() -> None:
    keys = [phase.key for phase in DESIGN_PHASES]
    assert keys == ["diagnose", "organize", "discussions", "dates", "publish"]
    assert [phase.writes for phase in DESIGN_PHASES] == [False, True, True, True, True]


# -- previewing each phase -------------------------------------------------


@pytest.mark.asyncio
async def test_diagnose_preview_shows_health_table() -> None:
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test() as pilot:
        screen = await preview_phase(app, pilot, 1)
        assert "Unpublished modules" in table_text(screen)
        assert COURSE_NAME in str(screen.query_one("#phase-summary").render())
        assert canvas.writes == []


@pytest.mark.parametrize(
    ("number", "expected_cell"),
    [
        (2, "U01R01 Read"),
        (3, "Getting to Know You"),
        (5, "U01A01 Task"),
    ],
    ids=["organize", "discussions", "publish"],
)
@pytest.mark.asyncio
async def test_write_phase_preview_lists_changes_and_writes_nothing(
    workspace: Path, number: int, expected_cell: str
) -> None:
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await preview_phase(app, pilot, number)
        assert expected_cell in table_text(screen)
        assert "Preview (dry run)" in str(screen.query_one("#phase-summary").render())
    assert canvas.writes == []
    assert backup_files(workspace) == []


@pytest.mark.asyncio
async def test_dates_preview_reads_the_schedule_file(workspace: Path, tmp_path: Path) -> None:
    schedule = tmp_path / "schedule.tsv"
    schedule.write_text(SCHEDULE_TEXT, encoding="utf-8")
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test() as pilot:
        screen = await wait_for_design_screen(app, pilot)
        await pilot.press("4")
        screen.query_one("#schedule-file", Input).value = str(schedule)
        screen = await preview_phase(app, pilot, 4)
        assert "2030-09-13T12:00:00" in table_text(screen)  # no Z suffix
    assert canvas.writes == []
    assert backup_files(workspace) == []


@pytest.mark.asyncio
async def test_typing_q_in_the_schedule_path_does_not_quit(workspace: Path) -> None:
    app = make_design_app(FakeDesignCanvas())
    async with app.run_test() as pilot:
        screen = await wait_for_design_screen(app, pilot)
        await pilot.press("4")
        screen.query_one("#schedule-file", Input).focus()
        await pilot.press("q", "n")
        assert screen.query_one("#schedule-file", Input).value == "qn"
        assert app.is_running and screen.current_phase.key == "dates"


@pytest.mark.asyncio
async def test_dates_preview_without_a_schedule_says_so(workspace: Path) -> None:
    app = make_design_app(FakeDesignCanvas())
    async with app.run_test() as pilot:
        screen = await wait_for_design_screen(app, pilot)
        await pilot.press("4", "v")
        await wait_until(
            pilot,
            lambda: "schedule file" in str(screen.query_one("#phase-summary").render()),
            "missing-schedule message",
        )
        assert "dates" not in screen.previews
