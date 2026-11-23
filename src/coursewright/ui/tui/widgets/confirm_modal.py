"""Reusable confirmation dialog for the Coursewright TUI.

Usage from any screen::

    def action_delete(self) -> None:
        self.app.push_screen(
            ConfirmModal("Delete this item?"),
            callback=self._handle_delete_confirmed,
        )

    def _handle_delete_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            ...  # do the thing
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmModal(ModalScreen[bool]):
    """A yes/no confirmation dialog that dismisses with ``True`` or ``False``.

    Parameters
    ----------
    prompt_text:
        The question to display (Rich markup supported).
    yes_label:
        Label for the confirm button.  Defaults to ``"Yes"``.
    no_label:
        Label for the cancel button.  Defaults to ``"No"``.
    """

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }

    ConfirmModal > Vertical {
        width: 60;
        height: auto;
        max-height: 20;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }

    ConfirmModal > Vertical > Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }

    ConfirmModal > Vertical > Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
    }

    ConfirmModal > Vertical > Horizontal > Button {
        margin: 0 2;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        prompt_text: str = "Are you sure?",
        yes_label: str = "Yes",
        no_label: str = "No",
    ) -> None:
        super().__init__()
        self._prompt_text = prompt_text
        self._yes_label = yes_label
        self._no_label = no_label

    def compose(self) -> ComposeResult:
        """Build the modal dialog layout."""
        with Vertical():
            yield Label(self._prompt_text)
            with Horizontal():
                yield Button(
                    self._yes_label,
                    id="confirm-yes",
                    variant="success",
                )
                yield Button(
                    self._no_label,
                    id="confirm-no",
                    variant="error",
                )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with True (yes) or False (no)."""
        self.dismiss(event.button.id == "confirm-yes")

    def action_confirm(self) -> None:
        """Keyboard shortcut ``y`` — confirm."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Keyboard shortcut ``n`` or ``Escape`` — cancel."""
        self.dismiss(False)
