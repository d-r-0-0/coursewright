"""Context-sensitive key-binding help bar for the footer area.

This is a thin wrapper around Textual's built-in ``Footer`` widget.
It exists so that we have a single place to customise the footer
style across all screens if needed.

Usage::

    from coursewright.ui.tui.widgets.help_bar import HelpBar

    class MyScreen(CoursewrightScreen):
        def compose(self) -> ComposeResult:
            yield Header()
            yield ...  # screen content
            yield HelpBar()
"""

from __future__ import annotations

from textual.widgets import Footer


class HelpBar(Footer):
    """Footer bar that shows available key-bindings.

    Inherits from Textual's ``Footer`` which already reads
    ``BINDINGS`` from the active screen and displays them.
    Custom styling can be added here later.
    """

    DEFAULT_CSS = """
    HelpBar {
        dock: bottom;
    }
    """
