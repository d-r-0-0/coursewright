"""Semester picker modal for the Coursewright TUI.

Allows the user to select an academic semester or "All Semesters".
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from coursewright.core.config.semester import (
    build_semester_choices,
    detect_current_semester,
)


class SemesterPickerScreen(ModalScreen[str | None]):
    """A modal dialog for picking a semester.

    Returns the semester string (e.g. "2026 Winter"), ``None`` if
    "All Semesters" was chosen, or ``"__CANCEL__"`` if dismissed with
    Escape (``None`` is taken, so cancel needs its own value).
    """

    DEFAULT_CSS = """
    SemesterPickerScreen {
        align: center middle;
    }

    SemesterPickerScreen > Vertical {
        width: 1fr;
        min-width: 36;
        max-width: 72;
        height: auto;
        max-height: 30;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }

    SemesterPickerScreen > Vertical > Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
        text-style: bold;
    }

    SemesterPickerScreen OptionList {
        height: auto;
        max-height: 20;
        border: none;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        """Build the picker layout."""
        semesters = build_semester_choices()
        current = detect_current_semester()

        options = [Option("📅 All Semesters", id="all")]

        # Track which index to highlight after mount
        self._current_semester_index = 0
        for idx, sem in enumerate(semesters, 1):
            label = sem
            if sem == current:
                label = f"{sem} (current)"
                self._current_semester_index = idx
            options.append(Option(label, id=sem))

        with Vertical():
            yield Label("Select a Semester")
            yield OptionList(*options, id="semester-options")

    def on_mount(self) -> None:
        """Highlight the current semester after the widget is mounted."""
        option_list = self.query_one("#semester-options", OptionList)
        option_list.highlighted = self._current_semester_index

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the selected semester ID."""
        selected_id = event.option.id
        if selected_id == "all":
            self.dismiss(None)
        else:
            self.dismiss(str(selected_id))

    def action_cancel(self) -> None:
        """Dismiss without changing anything."""
        self.dismiss("__CANCEL__")
