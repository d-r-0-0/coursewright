"""Simple text input modal for the Coursewright TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class InputModal(ModalScreen[str | None]):
    """A modal dialog with a single text input field.

    Returns the entered string or ``None`` if cancelled.
    """

    DEFAULT_CSS = """
    InputModal {
        align: center middle;
    }

    InputModal > Vertical {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }

    InputModal > Vertical > Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        prompt_text: str = "Enter value:",
        default_value: str = "",
    ) -> None:
        super().__init__()
        self._prompt_text = prompt_text
        self._default_value = default_value

    def compose(self) -> ComposeResult:
        """Build the modal layout."""
        with Vertical():
            yield Label(self._prompt_text)
            yield Input(
                value=self._default_value,
                placeholder="Type here...",
                id="modal-input",
            )

    def on_mount(self) -> None:
        """Focus the input field."""
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key inside the Input widget."""
        self.dismiss(event.value)

    def action_submit(self) -> None:
        """Dismiss with the current input value."""
        value = self.query_one(Input).value
        self.dismiss(value)

    def action_cancel(self) -> None:
        """Dismiss with None indicating cancellation."""
        self.dismiss(None)
