"""Tests for the Textual TUI reusable widgets.

These use Textual's ``Pilot`` testing framework to drive the widgets
headlessly.  Each test spins up a minimal ``App`` that mounts the
widget under test, then asserts on the result.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.help_bar import HelpBar

# ======================================================================
# Helpers — tiny apps that host a single widget under test
# ======================================================================


class ConfirmModalTestApp(App):
    """Minimal app that immediately pushes a ConfirmModal."""

    def __init__(self, prompt: str = "Proceed?") -> None:
        super().__init__()
        self._prompt = prompt
        self.modal_result: bool | None = None

    def compose(self) -> ComposeResult:
        yield Static("background")

    def on_mount(self) -> None:
        """Push the modal as soon as the app is mounted."""
        self.push_screen(
            ConfirmModal(self._prompt),
            callback=self._on_modal_dismissed,
        )

    def _on_modal_dismissed(self, result: bool) -> None:
        self.modal_result = result
        self.exit()


class HelpBarTestApp(App):
    """Minimal app that shows a HelpBar."""

    def compose(self) -> ComposeResult:
        yield Static("content")
        yield HelpBar()


# ======================================================================
# ConfirmModal tests
# ======================================================================


@pytest.mark.asyncio
async def test_confirm_modal_yes_button():
    """Clicking the Yes button should dismiss with True."""
    app = ConfirmModalTestApp("Delete everything?")
    async with app.run_test() as pilot:
        # Click the Yes button
        await pilot.click("#confirm-yes")
    assert app.modal_result is True


@pytest.mark.asyncio
async def test_confirm_modal_no_button():
    """Clicking the No button should dismiss with False."""
    app = ConfirmModalTestApp("Delete everything?")
    async with app.run_test() as pilot:
        await pilot.click("#confirm-no")
    assert app.modal_result is False


@pytest.mark.asyncio
async def test_confirm_modal_y_shortcut():
    """Pressing 'y' should dismiss with True."""
    app = ConfirmModalTestApp()
    async with app.run_test() as pilot:
        await pilot.press("y")
    assert app.modal_result is True


@pytest.mark.asyncio
async def test_confirm_modal_n_shortcut():
    """Pressing 'n' should dismiss with False."""
    app = ConfirmModalTestApp()
    async with app.run_test() as pilot:
        await pilot.press("n")
    assert app.modal_result is False


@pytest.mark.asyncio
async def test_confirm_modal_escape_cancels():
    """Pressing Escape should dismiss with False (cancel)."""
    app = ConfirmModalTestApp()
    async with app.run_test() as pilot:
        await pilot.press("escape")
    assert app.modal_result is False


@pytest.mark.asyncio
async def test_confirm_modal_shows_prompt_text():
    """The prompt text stored on the modal should match what we passed."""
    app = ConfirmModalTestApp("Really delete the database?")
    async with app.run_test() as pilot:
        # The modal is the active screen, not a child widget.
        modal_screen = app.screen
        assert isinstance(modal_screen, ConfirmModal)
        # Verify the prompt was stored correctly
        assert modal_screen._prompt_text == "Really delete the database?"
        # Clean up — dismiss the modal so the app exits
        await pilot.press("n")


# ======================================================================
# HelpBar tests
# ======================================================================


@pytest.mark.asyncio
async def test_help_bar_renders():
    """HelpBar should mount without errors."""
    app = HelpBarTestApp()
    async with app.run_test():
        help_bars = app.query(HelpBar)
        assert len(help_bars) == 1


# ======================================================================
# CoursewrightApp smoke test
# ======================================================================


@pytest.mark.asyncio
async def test_app_launches_and_quits(monkeypatch):
    """The main app shell should start and quit on 'q'."""
    from textual.screen import Screen

    from coursewright.ui.tui import app as app_module

    started_with: list[object] = []

    class StubInitScreen(Screen):
        """Stands in for InitScreen, which would load config and call Canvas."""

        def __init__(self, app_state) -> None:
            super().__init__()
            started_with.append(app_state)

    monkeypatch.setattr(app_module, "InitScreen", StubInitScreen)

    app = app_module.CoursewrightApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert started_with == [app.app_state]
        assert app.title == "Coursewright"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_app_state_defaults():
    """AppState should initialise with sensible defaults."""
    from coursewright.ui.tui.state import AppState

    state = AppState()
    assert state.courses == []
    assert state.filter_status == "active"
    assert state.show_current_semester_only is True
    assert state.current_semester  # should be a non-empty string
    assert state.canvas_client is None


@pytest.mark.asyncio
async def test_base_screen_quit_action():
    """CoursewrightScreen.action_request_quit should exit the app."""
    from coursewright.ui.tui.screens.base import (
        CoursewrightScreen,
    )

    class TestScreenApp(App):
        def on_mount(self) -> None:
            self.push_screen(CoursewrightScreen())

    app = TestScreenApp()
    async with app.run_test() as pilot:
        await pilot.press("q")
