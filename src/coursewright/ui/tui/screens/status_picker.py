"""Status picker modal for the Coursewright TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option


class StatusPickerScreen(ModalScreen[str | None]):
    """A modal dialog for picking a course status filter.

    Returns "active", "completed", "all", or "__CANCEL__".
    """

    DEFAULT_CSS = """
    StatusPickerScreen {
        align: center middle;
    }

    StatusPickerScreen > Vertical {
        width: 1fr;
        min-width: 28;
        max-width: 56;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }

    StatusPickerScreen > Vertical > Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
        text-style: bold;
    }

    StatusPickerScreen OptionList {
        height: auto;
        border: none;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        """Build the picker layout."""
        with Vertical():
            yield Label("Filter by Status")
            yield OptionList(
                Option("Active", id="active"),
                Option("Completed", id="completed"),
                Option("All Statuses", id="all"),
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the selected status."""
        self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        """Dismiss with cancel sentinel."""
        self.dismiss("__CANCEL__")
