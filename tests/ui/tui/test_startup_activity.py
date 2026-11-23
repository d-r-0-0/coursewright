"""Remote activity is always visible in the Textual app.

docs/HOUSE_STYLE.md section 3 and the card's acceptance item 4: any
screen that triggers a remote call shows the transmission log or a
loading indicator while the call runs.  Each test here holds a fake
Canvas call open on a ``threading.Event`` so the "while it runs" state
can be inspected with the Textual pilot, then releases it.

Also covers the application shell: its name and the ``tui`` command.
"""

from __future__ import annotations

from threading import Event

import pytest
from textual.widgets import LoadingIndicator
from typer.testing import CliRunner

from coursewright.core.config.settings import AppConfig
from coursewright.ui.cli.main import app as command_line_app
from coursewright.ui.tui.app import CoursewrightApp
from coursewright.ui.tui.screens.course_list import CourseListScreen
from coursewright.ui.tui.screens.init_screen import InitScreen
from coursewright.ui.tui.state import AppState

from .activity_helpers import (
    HeldCanvasClient,
    OneScreenApp,
    find_screen,
    make_course_dict,
    wait_until,
)


def test_app_is_named_coursewright() -> None:
    assert CoursewrightApp.TITLE == "Coursewright"
    assert "iscussorator" not in CoursewrightApp.SUB_TITLE


def test_tui_command_launches_the_application(monkeypatch) -> None:
    launched: list[bool] = []
    monkeypatch.setattr(
        "coursewright.ui.tui.app.run_tui", lambda **_kwargs: launched.append(True)
    )

    result = CliRunner().invoke(command_line_app, ["tui"])

    assert result.exit_code == 0
    assert launched == [True]


def test_tui_command_is_listed_in_help() -> None:
    result = CliRunner().invoke(command_line_app, ["--help"])
    assert "tui" in result.output


# ----------------------------------------------------------------------
# Start-up: the Init screen
# ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("held_step", ["connection", "courses"])
async def test_init_screen_shows_loading_indicator_while_connecting(monkeypatch, held_step) -> None:
    release = Event()
    client = HeldCanvasClient(release, courses=[make_course_dict()])

    def hold_connection_open(**_keyword_arguments):
        if held_step == "connection":
            client.entered.set()
            release.wait(timeout=5)
        return client

    # Never let a test write a config.yaml into the working directory.
    monkeypatch.setattr(AppConfig, "load_from_file", classmethod(lambda cls, *a, **k: cls()))
    monkeypatch.setattr(
        "coursewright.core.canvas.client.create_canvas_client", hold_connection_open
    )
    monkeypatch.setattr("coursewright.core.llm.client.create_client", lambda **kwargs: object())
    state = AppState()
    app = OneScreenApp(InitScreen(state), state)
    async with app.run_test() as pilot:
        await wait_until(pilot, client.entered.is_set, "Canvas connection started")
        init_screen = find_screen(app, InitScreen)
        indicator = init_screen.query_one("#init-loading", LoadingIndicator)
        assert indicator.display is True
        if held_step == "connection":
            assert "Connecting to Canvas" in str(init_screen.query_one("#status-canvas").render())
        else:
            assert "Loading courses" in str(init_screen.query_one("#status-courses").render())

        release.set()
        await wait_until(pilot, lambda: state.canvas_client is client, "client stored")
        await wait_until(
            pilot,
            lambda: find_screen(app, CourseListScreen) is not None,
            "course list pushed",
        )
        assert indicator.display is False
        # Let the freshly pushed course list finish mounting before the app
        # shuts down, so Textual's Header is not torn down mid-mount.
        await pilot.pause(0.2)


@pytest.mark.asyncio
async def test_init_screen_hides_loading_indicator_when_connection_fails(monkeypatch) -> None:
    monkeypatch.setattr(AppConfig, "load_from_file", classmethod(lambda cls, *a, **k: cls()))
    monkeypatch.setattr(
        "coursewright.core.canvas.client.create_canvas_client", lambda **_kwargs: None
    )
    state = AppState()
    app = OneScreenApp(InitScreen(state), state)
    async with app.run_test() as pilot:
        init_screen = find_screen(app, InitScreen)
        await wait_until(
            pilot,
            lambda: "Initialisation Failed" in str(init_screen.query_one("#init-result").render()),
            "failure panel",
        )
        await wait_until(
            pilot,
            lambda: init_screen.query_one("#init-loading").display is False,
            "indicator hidden",
        )


# ----------------------------------------------------------------------
