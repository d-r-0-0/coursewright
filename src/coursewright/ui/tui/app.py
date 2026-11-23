"""CoursewrightApp — top-level Textual application.

Owns session state (Canvas client, course list, filters) and
coordinates navigation between Screens.  Launched from the Typer
command line with ``coursewright tui``.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from coursewright.core.config import AppConfig
from coursewright.ui.tui.screens.init_screen import InitScreen
from coursewright.ui.tui.state import AppState

# ---------------------------------------------------------------------------
# Stylesheet lives alongside this file in styles/app.tcss
# ---------------------------------------------------------------------------
STYLES_DIR = Path(__file__).parent / "styles"


class CoursewrightApp(App):
    """Textual application shell for Coursewright.

    On startup the app pushes ``InitScreen`` which handles Canvas
    connection, LLM initialisation, and course loading.  The shared
    ``AppState`` instance is owned here and passed to every screen.
    """

    TITLE = "Coursewright"
    SUB_TITLE = "AI teaching assistant for Canvas"
    CSS_PATH = STYLES_DIR / "app.tcss"

    # -- Key bindings shown in the footer -----------------------------------
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
    ]

    def __init__(self, config: AppConfig | None = None, config_file: str = "config.yaml") -> None:
        super().__init__()
        # Shared mutable state that every screen can read/write.
        self.app_state = AppState(
            config=config if config is not None else AppConfig.load_from_file(config_file),
            config_file=config_file,
        )

    # -- Graceful shutdown --------------------------------------------------
    def action_quit(self) -> None:
        """Cancel any running background workers, then exit.

        Without this, @work(thread=True) workers blocked on Canvas
        API I/O keep their threads alive and the process hangs after
        the TUI has already visually closed.
        """
        self.workers.cancel_all()
        self.exit()

    # -- Compose the initial widget tree ------------------------------------
    def compose(self) -> ComposeResult:
        """Build the chrome — header + footer only.

        The real content comes from ``InitScreen`` which is pushed
        in ``on_mount``.
        """
        yield Header(show_clock=True)
        yield Footer()

    # -- Push the init screen on startup ------------------------------------
    def on_mount(self) -> None:
        """Push the initialisation screen as the first thing the user sees."""
        self.push_screen(InitScreen(self.app_state))


def run_tui(config: AppConfig | None = None, config_file: str = "config.yaml") -> None:
    """Start the Textual application and block until it exits."""
    CoursewrightApp(config=config, config_file=config_file).run()
