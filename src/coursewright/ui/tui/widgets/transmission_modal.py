"""Reusable transmission modal for long-running Canvas/LLM operations.

This is the Textual face of docs/HOUSE_STYLE.md section 3: while a
worker talks to a remote service, the modal shows a loading indicator
and the live transmission log.  The log lines come from
``coursewright.core.traffic``, the bus every client publishes to.
"""

from __future__ import annotations

from contextlib import suppress

from rich.markup import escape
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import LoadingIndicator, RichLog, Static

from coursewright.core import traffic


class TransmissionModal(ModalScreen[None]):
    """Modal overlay that shows live progress for network transmissions."""

    DEFAULT_CSS = """
    TransmissionModal {
        align: center middle;
    }
    #transmission-modal-panel {
        width: 100;
        height: 30;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #transmission-spinner {
        height: 1;
    }
    #transmission-status {
        height: 1;
        margin: 1 0 0 0;
    }
    #transmission-log {
        height: 1fr;
        margin: 1 0 0 0;
        color: $text-muted;
        background: $surface;
        border: none;
    }
    """

    BINDINGS = [Binding("escape", "dismiss_if_error", "Dismiss", show=False)]

    # Keep at most this many lines in the live log so a long batch run
    # doesn't blow the modal off the screen or eat memory.  We trim the
    # oldest entries first (FIFO) — the most recent chatter is what
    # the user actually wants to see.  RichLog handles its own
    # scrollback; this cap mirrors it for the in-memory backing list
    # that tests inspect.
    MAX_LOG_LINES = 500

    def __init__(self, status_text: str) -> None:
        super().__init__()
        self._status_text = status_text
        self._has_error = False
        self._log_lines: list[str] = []
        self._traffic_callback = self._handle_traffic_event

    def compose(self):
        with Vertical(id="transmission-modal-panel"):
            yield LoadingIndicator(id="transmission-spinner")
            yield Static(self._status_text, id="transmission-status")
            # RichLog auto-scrolls to the bottom and keeps history,
            # so the log feels like a live wire-capture instead of a
            # one-screen snapshot that gets stuck once it fills up.
            yield RichLog(
                id="transmission-log",
                highlight=False,
                markup=True,
                wrap=False,
                max_lines=self.MAX_LOG_LINES,
                auto_scroll=True,
            )

    def on_mount(self) -> None:
        """Subscribe to the traffic bus so live API chatter renders here."""
        traffic.subscribe(self._traffic_callback)

    def on_unmount(self) -> None:
        """Unsubscribe so a popped modal stops eating events."""
        traffic.unsubscribe(self._traffic_callback)

    def _handle_traffic_event(self, event: traffic.TrafficEvent) -> None:
        """Marshal a worker-thread traffic event back to the UI thread.

        Publishers (Canvas client, LLM client) typically call us from
        worker threads — Textual widgets must only mutate from the app
        loop, so we route through `call_from_thread`.  When publish
        happens to come from the loop thread itself (tests, or a sync
        path), `call_from_thread` raises; we then fall back to a
        direct call which is safe on the loop thread.
        """
        formatted_line = event.format_log_line()
        try:
            self.app.call_from_thread(self.append_log_line, formatted_line)
            return
        except Exception:
            pass
        with suppress(Exception):
            # The modal may be in the process of being torn down
            # (no running app, no event loop).  Drop quietly.
            self.append_log_line(formatted_line)

    def update_status_text(self, status_text: str) -> None:
        """Replace the one-line status text."""
        self.query_one("#transmission-status", Static).update(status_text)

    def append_log_line(self, log_line: str) -> None:
        """Append one line to the modal log area.

        We keep an in-memory mirror in ``_log_lines`` so tests (and
        any future "save log" feature) can inspect history without
        scraping the widget.  The visible widget is a ``RichLog``
        which auto-scrolls to the latest entry.

        A payload echo can contain square brackets that are not Rich
        markup; if the line does not parse, it is shown as plain text
        rather than being dropped.
        """
        self._log_lines.append(log_line)
        if len(self._log_lines) > self.MAX_LOG_LINES:
            self._log_lines = self._log_lines[-self.MAX_LOG_LINES :]
        with suppress(Exception):
            # During teardown the widget may already be detached.
            # Don't let that break the publisher path.
            log_widget = self.query_one("#transmission-log", RichLog)
            try:
                log_widget.write(log_line)
            except Exception:
                log_widget.write(escape(log_line))

    def mark_error(self, error_text: str) -> None:
        """Switch modal to error state and keep it open for escape dismissal."""
        self._has_error = True
        self.update_status_text(f"[red]{escape(error_text)}[/red]")
        self.append_log_line("[yellow]Press Escape to close this dialog.[/yellow]")

    def action_dismiss_if_error(self) -> None:
        """Allow dismissal only when the modal is in an error state."""
        if self._has_error:
            self.dismiss(None)
