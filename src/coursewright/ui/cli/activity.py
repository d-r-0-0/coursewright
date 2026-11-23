"""Show remote activity on the command line while a command waits.

docs/HOUSE_STYLE.md section 3: whenever Coursewright talks to a remote
service, the person at the keyboard can see that something is
happening.  Clients publish a traffic event for every round trip
(``coursewright.core.traffic``); this module is the command line's
subscriber.  Wrap any slow library call::

    with RemoteActivityDisplay("Fetching submissions") as activity:
        submissions = client.get_assignment_submissions(...)

and a Rich spinner names the service and operation in flight, with a
running count of finished requests and the last status.  When the
amount of work is known, pass ``total=`` (or call ``set_total`` once
it is learned, e.g. after the first page) and call ``advance()`` per
finished item: the spinner gains a progress bar with counts.

What is deliberately NOT shown (card acceptance 5, AGENTS.md 3.13):

* payload chunk events (request and response bodies, prompts) are
  ignored outright, so no body text reaches the terminal;
* query strings are dropped, so no ``access_token=`` or search term;
* path segments that are not plain words (ids, logins, addresses)
  are replaced with ``:id``, so no student identifier;
* everything that is shown is escaped, so nothing in an event can
  inject Rich markup.

When the console is not a terminal (output piped or redirected) the
display stays completely quiet: it does not subscribe and writes
nothing, so captured output stays clean.
"""

from __future__ import annotations

import re
from threading import Lock
from types import TracebackType

from rich.console import Console, RenderableType
from rich.live import Live
from rich.markup import escape
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from coursewright.core import traffic

# How each `TrafficEvent.source` is named on screen.  An unknown
# source is shown title-cased, so a new client needs no change here.
SERVICE_DISPLAY_NAMES = {"canvas": "Canvas", "llm": "LLM"}

# A path segment is shown only when it is a plain word: letters,
# underscores, dots and hyphens ("courses", "chat.completions").
# Anything else (digits, "sis_login_id:...", an address) is treated as
# an identifier and masked.
_PLAIN_WORD_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z_.\-]*")
MASKED_SEGMENT = ":id"

MAX_OPERATION_LENGTH = 60
MAX_METHOD_LENGTH = 40
MAX_SERVICE_LENGTH = 24
PROGRESS_BAR_WIDTH = 30


def _clip(text: str, max_length: int) -> str:
    """Cut `text` to `max_length` characters, ending in "..." if cut."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def describe_service(source: str) -> str:
    """Return the on-screen name of the service behind an event source."""
    known_name = SERVICE_DISPLAY_NAMES.get(source.lower())
    return known_name or _clip(source.title(), MAX_SERVICE_LENGTH)


def describe_operation(target: str) -> str:
    """Reduce an event target to an operation name that is safe to show.

    ``courses/101/users/2002?access_token=abc`` becomes
    ``courses/:id/users/:id``: the query string is dropped and every
    segment that is not a plain word is masked.
    """
    path_only = target.split("?", 1)[0].split("#", 1)[0]
    shown_segments = [
        segment if _PLAIN_WORD_SEGMENT.fullmatch(segment) else MASKED_SEGMENT
        for segment in path_only.split("/")
        if segment
    ]
    return _clip("/".join(shown_segments), MAX_OPERATION_LENGTH)


class RemoteActivityDisplay:
    """Context manager: a live spinner (and progress bar) of remote calls.

    `description` says what the command is doing, in the instructor's
    words ("Fetching submissions").  `total` is the number of items
    of work when known; `console` defaults to standard output.

    Traffic events arrive on whatever thread the client runs on, so
    all state is guarded by a lock; Rich's `Live` does its own locking
    for the drawing.
    """

    def __init__(
        self,
        description: str,
        *,
        total: int | None = None,
        console: Console | None = None,
    ) -> None:
        self.description = description
        self.console = console if console is not None else Console()
        self.total = total
        self.completed_steps = 0
        self.finished_request_count = 0
        self.last_status_label = ""
        self._last_status_style = "dim"
        self._current_call_label = ""
        self._state_lock = Lock()
        self._spinner = Spinner("dots", style="cyan")
        self._live: Live | None = None

    # -- context manager ------------------------------------------------

    def __enter__(self) -> RemoteActivityDisplay:
        if not self.console.is_terminal:
            # Piped or redirected: stay silent, do not even subscribe.
            return self
        self._live = Live(
            self._build_renderable(),
            console=self.console,
            transient=True,
            refresh_per_second=12.5,
        )
        self._live.start(refresh=True)
        traffic.subscribe(self._handle_traffic_event)
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        traffic.unsubscribe(self._handle_traffic_event)
        if self._live is not None:
            self._live.stop()
            self._live = None

    # -- progress, driven by the calling command ------------------------

    def set_total(self, total: int | None) -> None:
        """Say how many items of work there are, once that is known."""
        with self._state_lock:
            self.total = total
        self._redraw()

    def advance(self, steps: int = 1) -> None:
        """Record `steps` more items of work as finished."""
        with self._state_lock:
            self.completed_steps += steps
        self._redraw()

    # -- traffic, driven by the bus -------------------------------------

    def _handle_traffic_event(self, event: traffic.TrafficEvent) -> None:
        """Fold one traffic event into the display."""
        if event.status in (
            traffic.STATUS_OUTBOUND_CHUNK,
            traffic.STATUS_INBOUND_CHUNK,
        ):
            # Body and prompt snippets never reach the command line.
            # `event.payload` is not read anywhere in this module.
            return
        call_label = (
            f"[bold cyan]{escape(describe_service(event.source))}[/bold cyan] "
            f"{escape(_clip(event.method, MAX_METHOD_LENGTH))} "
            f"{escape(describe_operation(event.target))}"
        )
        with self._state_lock:
            self._current_call_label = call_label
            # An in-flight event only names the call; the count and
            # the last status stand until the call finishes.
            if event.status > 0:
                self.finished_request_count += 1
                self.last_status_label = str(event.status)
                succeeded = 200 <= event.status < 300
                self._last_status_style = "green" if succeeded else "red"
            elif event.status != traffic.STATUS_IN_FLIGHT:
                self.finished_request_count += 1
                self.last_status_label = "error"
                self._last_status_style = "red"
        self._redraw()

    # -- drawing --------------------------------------------------------

    def _build_status_markup(self) -> str:
        """Return the one-line status text, as Rich markup."""
        with self._state_lock:
            parts = [f"[bold]{escape(self.description)}[/bold]"]
            if self._current_call_label:
                parts.append(self._current_call_label)
            if self.finished_request_count:
                noun = "request" if self.finished_request_count == 1 else "requests"
                parts.append(f"[dim]{self.finished_request_count} {noun}[/dim]")
                style = self._last_status_style
                parts.append(f"[dim]last[/dim] [{style}]{self.last_status_label}[/{style}]")
            elif self._current_call_label:
                parts.append("[dim yellow]requesting…[/dim yellow]")
        return " [dim]·[/dim] ".join(parts)

    def _build_renderable(self) -> RenderableType:
        """Return the spinner line, plus a progress bar if a total is known."""
        self._spinner.update(text=Text.from_markup(self._build_status_markup()))
        with self._state_lock:
            total = self.total
            completed_steps = self.completed_steps
        if total is None:
            return self._spinner
        layout = Table.grid(padding=(0, 1))
        layout.add_row(
            self._spinner,
            ProgressBar(
                total=max(total, 1),
                completed=min(completed_steps, max(total, 1)),
                width=PROGRESS_BAR_WIDTH,
            ),
            Text(f"{completed_steps}/{total}", style="bold"),
        )
        return layout

    def _redraw(self) -> None:
        """Push the current state to the terminal, if one is attached."""
        live = self._live
        if live is not None:
            live.update(self._build_renderable(), refresh=True)
