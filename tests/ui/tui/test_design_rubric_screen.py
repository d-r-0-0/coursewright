"""Rubric and standards-audit screen.

The bundled default rubric is loaded from package data; the audit runs
against ``FakeDesignCanvas`` and never writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coursewright.core import traffic
from coursewright.design.standards import DEFAULT_RUBRIC_ID, load_rubric
from coursewright.ui.tui.screens.design_rubric import RubricAuditScreen

from .activity_helpers import OneScreenApp, find_screen, wait_until
from .design_fakes import (
    FakeDesignCanvas,
    make_design_app,
    make_design_state,
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


async def wait_for_audit(app, pilot) -> RubricAuditScreen:
    await wait_until(pilot, lambda: isinstance(app.screen, RubricAuditScreen), "audit screen")
    screen = app.screen
    await wait_until(pilot, lambda: bool(screen.results), "audit results")
    await wait_until(pilot, lambda: app.screen is screen, "transmission log closed")
    return screen


@pytest.mark.parametrize("opener", ["u", "button"])
@pytest.mark.asyncio
async def test_rubric_audit_opens_from_the_design_flow(workspace: Path, opener: str) -> None:
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for_design_screen(app, pilot)
        if opener == "u":
            await pilot.press("u")
        else:
            await pilot.click("#open-rubric-audit")
        screen = await wait_for_audit(app, pilot)
        assert screen.rubric is not None
    assert canvas.writes == []


@pytest.mark.asyncio
async def test_rubric_table_lists_every_standard(workspace: Path) -> None:
    rubric = load_rubric(DEFAULT_RUBRIC_ID)
    state = make_design_state(FakeDesignCanvas())
    app = OneScreenApp(RubricAuditScreen(state), state)
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await wait_for_audit(app, pilot)
        info = str(screen.query_one("#rubric-info").render())
        assert rubric.name in info
        assert f"{len(rubric.standards)} standard(s)" in info
        standards = table_text(screen, "#rubric-standards")
        for standard in rubric.standards:
            assert standard.id in standards


@pytest.mark.asyncio
async def test_audit_results_show_status_labels_and_counts(workspace: Path) -> None:
    state = make_design_state(FakeDesignCanvas())
    app = OneScreenApp(RubricAuditScreen(state), state)
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await wait_for_audit(app, pilot)
        results = table_text(screen, "#audit-results")
        assert len(screen.results) == len(screen.rubric.standards)
        assert any(label in results for label in ("Met", "Not Met", "Needs Human"))
        summary = str(screen.query_one("#audit-summary").render())
        for label in ("Met:", "Not Met:", "Needs Human:"):
            assert label in summary
        assert "nothing was written" in summary


@pytest.mark.asyncio
async def test_audit_can_run_again_with_r(workspace: Path) -> None:
    calls: list[int] = []
    canvas = FakeDesignCanvas()
    original_get_course = canvas.get_course

    def counting_get_course(course_id: int):
        calls.append(course_id)
        return original_get_course(course_id)

    canvas.get_course = counting_get_course
    state = make_design_state(canvas)
    app = OneScreenApp(RubricAuditScreen(state), state)
    async with app.run_test(size=(140, 50)) as pilot:
        await wait_for_audit(app, pilot)
        await pilot.press("r")
        await wait_until(pilot, lambda: len(calls) == 2, "second audit")


@pytest.mark.asyncio
async def test_unknown_rubric_is_a_clean_message(workspace: Path) -> None:
    state = make_design_state(FakeDesignCanvas())
    app = OneScreenApp(RubricAuditScreen(state, rubric_id="no-such-rubric"), state)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.rubric is None
        assert "Could not load rubric" in str(screen.query_one("#rubric-info").render())
        assert screen.results == []


@pytest.mark.asyncio
async def test_audit_failure_is_shown_not_swallowed(workspace: Path) -> None:
    canvas = FakeDesignCanvas()

    def missing_course(course_id: int):
        return None

    canvas.get_course = missing_course
    state = make_design_state(canvas)
    app = OneScreenApp(RubricAuditScreen(state), state)
    async with app.run_test() as pilot:
        await wait_until(pilot, lambda: find_screen(app, RubricAuditScreen), "screen")
        screen = find_screen(app, RubricAuditScreen)
        await wait_until(
            pilot,
            lambda: "Audit failed" in str(screen.query_one("#audit-summary").render()),
            "failure message",
        )
