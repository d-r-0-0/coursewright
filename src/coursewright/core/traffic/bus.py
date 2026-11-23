"""Tiny pub/sub bus for in-flight Canvas/LLM traffic events.

Ported from discussorator ``cli/tui/traffic_bus.py`` and moved out of
the user interface: clients in ``core`` publish here, and ``ui/``
subscribes and renders (docs/HOUSE_STYLE.md section 3).  The Textual
transmission log subscribes so users can SEE actual API chatter
(method, path, status, duration) streaming into the log while a
worker is hitting Canvas or an LLM provider; the command line shows a
spinner or progress bar (``coursewright.ui.cli.activity``).

The bus is process-global and threadsafe.  Publishers (Canvas client,
LLM client) call `publish(...)` from worker threads; subscribers
should marshal updates back to the UI thread themselves (e.g. via
Textual's `App.call_from_thread`).

Nothing here imports Rich.  `format_log_line` returns a plain string
that happens to contain Rich markup, for a subscriber in ``ui/`` to
hand to its own console.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class TrafficEvent:
    """One observable network round-trip from a Canvas or LLM client.

    `source` is "canvas" or "llm".  For Canvas, `method` is the HTTP
    verb and `target` is the API path (host stripped).  For LLM,
    `method` is the model identifier and `target` is a short label
    like "chat.completions".

    `status` semantics:
      * positive  → completed call, value is the HTTP status code
      * 0         → in-flight (request sent, response not yet returned)
      * -1        → errored out (exception, timeout, connection reset)
      * -2        → outbound payload chunk (echo of request body / prompt)
      * -3        → inbound payload chunk (echo of response body / content)

    `duration_ms` is the round-trip wall-clock time in milliseconds.
    For in-flight and chunk events it's 0.

    `payload` is set only on chunk events (-2 / -3); for those, it
    carries the actual snippet of text to echo into the log so users
    get a "Matrix-style" scrolling view of what's flying over the
    wire.  Other event kinds leave it empty.
    """

    source: str
    method: str
    target: str
    status: int
    duration_ms: int
    payload: str = ""

    def format_log_line(self) -> str:
        """Render this event as a Rich-markup string for the modal log.

        We deliberately emit BOTH an in-flight line (→) and a
        completion line (←) for every call so users see the chatter
        flow in real time — not just a single terse summary line.
        For chunk events (status -2/-3) we render dim arrows with the
        payload snippet so the log feels like a live wire-capture.
        """
        # Escape every untrusted label without importing a UI library.
        method = self.method.replace("[", "\\[")
        target = self.target.replace("[", "\\[")
        # Payload echo lines come first because both canvas and llm
        # use the same indented dim style — only the accent color
        # differs.
        if self.status == -2 or self.status == -3:
            outbound = self.status == -2
            arrow = "↑" if outbound else "↓"
            if self.source == "llm":
                accent = "bright_magenta" if outbound else "bright_green"
            else:
                accent = "bright_cyan" if outbound else "bright_green"
            return f"  [{accent}]{arrow}[/{accent}] [dim]Private body omitted.[/dim]"
        if self.source == "canvas":
            if self.status == 0:
                return (
                    f"[bold cyan]→ {method:<5}[/bold cyan] {target} "
                    f"[dim yellow]requesting…[/dim yellow]"
                )
            ok = 200 <= self.status < 300
            arrow_color = "cyan" if ok else "red"
            status_color = "green" if ok else "red"
            return (
                f"[{arrow_color}]← {method:<5}[/{arrow_color}] {target} "
                f"[{status_color}]{self.status}[/{status_color}] "
                f"[dim]· {self.duration_ms}ms[/dim]"
            )
        # LLM source
        if self.status == 0:
            return (
                f"[bold magenta]→ {method}[/bold magenta] {target} "
                f"[dim yellow]requesting…[/dim yellow]"
            )
        if self.status < 0:
            return (
                f"[red]← {method}[/red] {target} [red]ERROR[/red] [dim]· {self.duration_ms}ms[/dim]"
            )
        return (
            f"[magenta]← {method}[/magenta] {target} "
            f"[green]{self.status}[/green] [dim]· {self.duration_ms}ms[/dim]"
        )


_traffic_subscribers: list[Callable[[TrafficEvent], None]] = []
_traffic_subscribers_lock = Lock()


def subscribe(callback: Callable[[TrafficEvent], None]) -> None:
    """Register a callback to receive every published `TrafficEvent`."""
    with _traffic_subscribers_lock:
        if callback not in _traffic_subscribers:
            _traffic_subscribers.append(callback)


def unsubscribe(callback: Callable[[TrafficEvent], None]) -> None:
    """Remove a previously-registered callback.  Silent if unknown."""
    with _traffic_subscribers_lock:
        if callback in _traffic_subscribers:
            _traffic_subscribers.remove(callback)


def publish(event: TrafficEvent) -> None:
    """Fan an event out to every subscriber.  Subscriber errors are swallowed."""
    with _traffic_subscribers_lock:
        snapshot = list(_traffic_subscribers)
    for callback in snapshot:
        with suppress(Exception):
            # Never let a buggy subscriber take down the publisher's
            # request path.  The modal can disappear without warning
            # (e.g. screen popped) and we don't care.
            callback(event)


def reset_for_tests() -> None:
    """Clear all subscribers.  Test-only helper."""
    with _traffic_subscribers_lock:
        _traffic_subscribers.clear()


def publish_chunks(
    source: str,
    method: str,
    target: str,
    direction: str,
    text: str,
    *,
    max_chars: int = 420,
) -> None:
    """Compatibility hook: raw bodies are deliberately never published."""
    # Bodies may carry student records, prompts, secrets, or provider completions.
    # Keep this compatibility entry point inert; activity uses round-trip events.
    return
