"""Profile selection modal for the Coursewright TUI.

This small modal lets the user switch between the supported grading
profiles without needing command-line flags. The chosen profile is
persisted to config.yaml and applied to the next grading run.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ProfilePickerModal(ModalScreen[str | None]):
    """Choose between the supported grading profiles."""

    DEFAULT_CSS = """
    ProfilePickerModal {
        align: center middle;
    }

    ProfilePickerModal > Vertical {
        width: 80;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }

    ProfilePickerModal > Vertical > Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }

    ProfilePickerModal > Vertical > Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
    }

    ProfilePickerModal > Vertical > Horizontal > Button {
        margin: 0 2;
    }
    """

    BINDINGS = [
        Binding("1", "pick_auto", "Auto", show=False),
        Binding("2", "pick_legacy", "Legacy", show=False),
        Binding("3", "pick_ai", "AI Discussion", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, current_profile: str = "auto") -> None:
        super().__init__()
        self.current_profile = current_profile if current_profile else "auto"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                "Choose the grading profile for the next analysis run:\n"
                f"Current profile: {self.current_profile}\n"
                "Auto reads each discussion's instructions and picks for you.",
            )
            with Horizontal():
                yield Button("Auto-detect", id="profile-auto", variant="warning")
                yield Button("Legacy", id="profile-legacy", variant="primary")
                yield Button("AI Discussion", id="profile-ai", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with the selected profile."""
        if event.button.id == "profile-ai":
            self.dismiss("ai_discussion")
            return
        if event.button.id == "profile-auto":
            self.dismiss("auto")
            return
        self.dismiss("legacy")

    def action_pick_auto(self) -> None:
        """Keyboard shortcut for automatic per-discussion detection."""
        self.dismiss("auto")

    def action_pick_legacy(self) -> None:
        """Keyboard shortcut for the legacy profile."""
        self.dismiss("legacy")

    def action_pick_ai(self) -> None:
        """Keyboard shortcut for the AI discussion profile."""
        self.dismiss("ai_discussion")

    def action_cancel(self) -> None:
        """Close without changing anything."""
        self.dismiss(None)
