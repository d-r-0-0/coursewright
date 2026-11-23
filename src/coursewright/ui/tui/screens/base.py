"""Base screen with shared key-bindings and layout helpers.

Every Coursewright screen inherits from ``CoursewrightScreen`` so that
the quit / back shortcuts and the standard header+footer chrome are
consistent across the application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from textual.binding import Binding
from textual.screen import Screen

if TYPE_CHECKING:
    from coursewright.core.config import AppConfig
    from coursewright.ui.tui.state import AppState


class CoursewrightScreen(Screen):
    """Base screen that all Coursewright screens inherit from.

    Provides:
    * ``Escape`` → pop back to the previous screen (or quit if root).
    * ``q``      → quit the entire application.

    Subclasses override ``compose()`` to supply their own widget
    tree and add extra ``BINDINGS`` as needed.
    """

    BINDINGS = [
        Binding(
            "escape",
            "go_back",
            "Back",
            priority=True,
        ),
        Binding(
            "q",
            "request_quit",
            "Quit",
            priority=True,
        ),
    ]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_go_back(self) -> None:
        """Pop this screen and return to the previous one.

        If this is the only screen on the stack the app will quit
        instead (Textual's default behaviour for ``pop_screen`` on
        the root screen raises ``ScreenStackError``; we catch that
        and exit gracefully).
        """
        try:
            self.app.pop_screen()
        except Exception:
            # We are on the root screen — just quit.
            self.app.workers.cancel_all()
            self.app.exit()

    def action_request_quit(self) -> None:
        """Cancel running workers and quit the application."""
        self.app.workers.cancel_all()
        self.app.exit()

    def _persist_config_or_warn(
        self,
        state: AppState | None,
        fallback_config: AppConfig | None = None,
    ) -> bool:
        """Save configuration changes, warning instead of crashing on I/O errors.

        Returns ``True`` when the config file was written and ``False`` when
        saving failed (for example, a read-only or root-owned config file).
        The updated setting remains active in memory for the current session.
        """
        config_path = state.config_file if state is not None else "config.yaml"
        try:
            if state is not None:
                state.save_config()
            elif fallback_config is not None:
                fallback_config.save_to_file()
            else:
                return False
        except OSError as save_error:
            self.app.notify(
                f"Could not save {escape(str(config_path))}: {escape(str(save_error))}. "
                "Selection applies for this session only.",
                title="Config Not Saved",
                severity="warning",
                timeout=10,
            )
            return False
        return True
