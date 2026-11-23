"""Apply on the course-design write phases is always confirmed first.

These tests are the proof that the TUI never writes to Canvas without an
explicit ``ConfirmModal`` "yes" and a workspace for the backup:

* declining (button, ``n`` or Escape) writes nothing and saves no backup;
* confirming writes through the fake client after a backup is saved;
* with no ``COURSEWRIGHT_WORKSPACE`` Apply refuses before even asking;
* Apply needs a preview first, and the read-only phase offers none.

The last group shows remote calls are visible in the transmission log.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Input

from coursewright.core import traffic
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

from .activity_helpers import find_screen, wait_until
from .design_fakes import (
    SCHEDULE_TEXT,
    FakeDesignCanvas,
    backup_files,
    make_design_app,
    preview_phase,
    wait_for_design_screen,
)

WRITE_PHASES = {2: "update_module_item", 3: "create_discussion_topic", 5: "update_module"}


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


async def open_confirmation(app, pilot, number: int) -> ConfirmModal:
    await preview_phase(app, pilot, number)
    await pilot.press("a")
    await wait_until(pilot, lambda: isinstance(app.screen, ConfirmModal), "confirm modal")
    return app.screen


# -- declining writes nothing ----------------------------------------------


@pytest.mark.parametrize("decline_key", ["n", "escape"])
@pytest.mark.parametrize("number", sorted(WRITE_PHASES), ids=["organize", "discussions", "publish"])
@pytest.mark.asyncio
async def test_declining_apply_writes_nothing(
    workspace: Path, number: int, decline_key: str
) -> None:
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test(size=(140, 50)) as pilot:
        await open_confirmation(app, pilot, number)
        await pilot.press(decline_key)
        screen = await wait_for_design_screen(app, pilot)
        await pilot.pause()
        assert not screen.busy
    assert canvas.writes == []
    assert backup_files(workspace) == []


@pytest.mark.asyncio
async def test_cancel_button_writes_nothing(workspace: Path) -> None:
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test(size=(140, 50)) as pilot:
        await open_confirmation(app, pilot, 2)
        await pilot.click("#confirm-no")
        await wait_for_design_screen(app, pilot)
    assert canvas.writes == []


# -- confirming writes through the fake client ------------------------------


@pytest.mark.parametrize(
    ("number", "write_name"),
    sorted(WRITE_PHASES.items()),
    ids=["organize", "discussions", "publish"],
)
@pytest.mark.asyncio
async def test_confirming_apply_writes_after_a_backup(
    workspace: Path, number: int, write_name: str
) -> None:
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test(size=(140, 50)) as pilot:
        modal = await open_confirmation(app, pilot, number)
        assert "backup" in str(modal._prompt_text)
        await pilot.press("y")
        screen = await wait_for_design_screen(app, pilot)
        await wait_until(pilot, lambda: bool(canvas.writes) and not screen.busy, "the write")
        await wait_until(pilot, lambda: app.screen is screen, "transmission log closed")
        assert "Done." in str(screen.query_one("#phase-summary").render())
        # A fresh preview is needed before applying again.
        assert screen.current_phase.key not in screen.previews
    assert write_name in {name for name, _args in canvas.writes}
    assert backup_files(workspace) != []


@pytest.mark.asyncio
async def test_dates_apply_needs_confirmation(workspace: Path, tmp_path: Path) -> None:
    schedule = tmp_path / "schedule.tsv"
    schedule.write_text(SCHEDULE_TEXT, encoding="utf-8")
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await wait_for_design_screen(app, pilot)
        await pilot.press("4")
        screen.query_one("#schedule-file", Input).value = str(schedule)
        await open_confirmation(app, pilot, 4)
        await pilot.press("n")
        await wait_for_design_screen(app, pilot)
        assert canvas.writes == []
        await pilot.press("a", "y")
        await wait_until(pilot, lambda: bool(canvas.writes) and not screen.busy, "dates write")
    assert "update_module" in {name for name, _args in canvas.writes}


# -- refusing cleanly ------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_without_workspace_refuses_before_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test() as pilot:
        screen = await wait_for_design_screen(app, pilot)
        await pilot.press("2", "a")
        await pilot.pause()
        assert find_screen(app, ConfirmModal) is None
        assert "COURSEWRIGHT_WORKSPACE" in str(screen.query_one("#phase-summary").render())
        # Previewing a write phase is refused the same way (no backup home).
        await pilot.press("v")
        await wait_until(pilot, lambda: not screen.busy, "refused preview")
        await pilot.pause()
        assert "organize" not in screen.previews
    assert canvas.writes == []


@pytest.mark.asyncio
async def test_apply_needs_a_preview_first(workspace: Path) -> None:
    app = make_design_app(FakeDesignCanvas())
    async with app.run_test() as pilot:
        await wait_for_design_screen(app, pilot)
        await pilot.press("2", "a")
        await pilot.pause()
        assert find_screen(app, ConfirmModal) is None


@pytest.mark.asyncio
async def test_diagnose_has_nothing_to_apply(workspace: Path) -> None:
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test() as pilot:
        await preview_phase(app, pilot, 1)
        await pilot.press("a")
        await pilot.pause()
        assert find_screen(app, ConfirmModal) is None
    assert canvas.writes == []


# -- remote activity is visible --------------------------------------------


@pytest.mark.asyncio
async def test_preview_runs_under_the_transmission_log(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fake's traffic events reach the live log while a phase runs."""
    seen_lines: list[str] = []
    original_append = TransmissionModal.append_log_line

    def recording_append(self, line: str) -> None:
        seen_lines.append(line)
        original_append(self, line)

    monkeypatch.setattr(TransmissionModal, "append_log_line", recording_append)
    app = make_design_app(FakeDesignCanvas())
    async with app.run_test() as pilot:
        await preview_phase(app, pilot, 2)
    assert any("modules" in line for line in seen_lines)


@pytest.mark.asyncio
async def test_failed_phase_keeps_the_log_open_with_the_error(workspace: Path) -> None:
    canvas = FakeDesignCanvas()

    def broken_modules(course_id: int):
        raise RuntimeError("synthetic Canvas outage")

    canvas.get_modules = broken_modules
    app = make_design_app(canvas)
    async with app.run_test() as pilot:
        screen = await wait_for_design_screen(app, pilot)
        await pilot.press("3", "v")
        await wait_until(pilot, lambda: isinstance(app.screen, TransmissionModal), "log")
        modal = app.screen
        await wait_until(pilot, lambda: modal._has_error, "error shown in the log")
        assert "synthetic Canvas outage" in str(screen.query_one("#phase-summary").render())
        await pilot.press("escape")
        await wait_until(pilot, lambda: app.screen is screen, "log dismissed")
    assert canvas.writes == []


@pytest.mark.parametrize("change", ["content", "path", "remote_id", "remote_title"])
@pytest.mark.asyncio
async def test_apply_rejects_changed_reviewed_inputs(
    workspace: Path, tmp_path: Path, change: str
) -> None:
    """Changes after confirmation opens are caught before any backup or write."""
    schedule = tmp_path / "schedule.tsv"
    schedule.write_text(SCHEDULE_TEXT, encoding="utf-8")
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test(size=(140, 50)) as pilot:
        screen = await wait_for_design_screen(app, pilot)
        screen.query_one("#schedule-file", Input).value = str(schedule)
        await open_confirmation(app, pilot, 4)
        if change == "content":
            schedule.write_text(SCHEDULE_TEXT.replace("2030", "2031"), encoding="utf-8")
        elif change == "path":
            other = tmp_path / "other.tsv"
            other.write_text(SCHEDULE_TEXT, encoding="utf-8")
            screen.query_one("#schedule-file", Input).value = str(other)
        elif change == "remote_id":
            canvas.items[1][0]["content_id"] = 999
        else:
            canvas.items[1][0]["title"] = "Changed with same row count"
        app.screen.dismiss(True)
        await wait_until(pilot, lambda: isinstance(app.screen, TransmissionModal), "validation")
        await wait_until(pilot, lambda: app.screen._has_error, "stale preview rejected")
        assert "changed since preview" in str(screen.query_one("#phase-summary").render())
    assert canvas.writes == []
    assert backup_files(workspace) == []


@pytest.mark.asyncio
async def test_apply_validates_later_module_reads_before_first_write(workspace: Path) -> None:
    canvas = FakeDesignCanvas()
    app = make_design_app(canvas)
    async with app.run_test(size=(140, 50)) as pilot:
        await open_confirmation(app, pilot, 2)
        canvas.items[2] = [{"id": 33, "title": "New remote item", "position": 1}]
        app.screen.dismiss(True)
        await wait_until(pilot, lambda: isinstance(app.screen, TransmissionModal), "validation")
        await wait_until(pilot, lambda: app.screen._has_error, "changed late input")
    assert canvas.writes == []
    assert backup_files(workspace) == []
