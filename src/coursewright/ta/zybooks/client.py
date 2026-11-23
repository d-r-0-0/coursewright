"""Read-only zyBooks report probe and retrieval, as silent library functions.

discussorator's version of this module printed with Rich and prompted with
``console.input``.  Library code is silent (AGENTS.md 3.8), so here the two
workflows take two small callables instead:

* ``ask(prompt) -> str`` returns what the instructor typed;
* ``emit(event)`` receives a :class:`ClientEvent` to show.

``ui/`` supplies a Rich-backed ``ask`` and ``emit`` (panels, tables, a status
spinner for ``progress`` events); tests supply plain lists.  Nothing here
imports a terminal library.  Events carry column labels and counts only,
never student rows (the same privacy line as :func:`inspect_report`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from time import monotonic

from coursewright.ta.zybooks.models import RetrievalRequest
from coursewright.ta.zybooks.parser import ZyBooksReportError, inspect_report, validate_report
from coursewright.ta.zybooks.session import (
    ZyBooksAccessError,
    ZyBooksBrowserSession,
    ZyBooksReportNotReady,
)

PROBE_INTRODUCTION = (
    "Log in directly in the browser. At book selection, open your zyBook, "
    "then its Reporting tab. Select the sections and Until date/time. "
    "Book selection alone is not ready to fetch.\n\n"
    "This probe clicks Download report for you. It displays column names "
    "and a row count only, then deletes the temporary download.\n"
    "Login is reused while this browser is open, not saved across runs. "
    "No grades are calculated or sent to Canvas."
)

RETRIEVAL_INTRODUCTION = (
    "Log in in the app-owned browser, then return here. Retrieval handles Reporting, "
    "section selection, dates, report generation and the temporary download.\n"
    "Login is kept in memory only. No Canvas grades are written."
)


class ClientEventKind(StrEnum):
    """What a :class:`ClientEvent` asks ``ui/`` to show."""

    INTRODUCTION = "introduction"
    OPENING_BROWSER = "opening-browser"
    FETCHING = "fetching"
    PROGRESS = "progress"
    NOT_READY = "not-ready"
    COLUMNS = "columns"
    SUMMARY = "summary"
    STOPPED = "stopped"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ClientEvent:
    """One thing for the interface to show: a title, a message, maybe columns."""

    kind: ClientEventKind
    title: str = ""
    message: str = ""
    columns: tuple[str, ...] = ()


Ask = Callable[[str], str]
Emit = Callable[[ClientEvent], None]
SessionFactory = Callable[[], ZyBooksBrowserSession]


def _current_minute() -> datetime:
    """Return 'now' as a timezone-aware instant at minute precision."""
    return datetime.now().astimezone().replace(second=0, microsecond=0)


def run_probe(
    ask: Ask,
    emit: Emit,
    session_factory: SessionFactory = ZyBooksBrowserSession,
) -> int:
    """Capture report structure using a human-authenticated, app-owned browser.

    Returns a process-style exit code: 0 for quit or cancel, 1 for a stop.
    """
    emit(ClientEvent(ClientEventKind.INTRODUCTION, "Read-only report probe", PROBE_INTRODUCTION))
    session = session_factory()
    try:
        emit(ClientEvent(ClientEventKind.OPENING_BROWSER, message="Opening zyBooks browser..."))
        session.__enter__()
        while True:
            answer = (
                ask("When Reporting is ready, enter fetch (or quit): ").strip().casefold()
            )
            if answer == "quit":
                return 0
            if answer != "fetch":
                continue
            started_at = monotonic()
            try:
                emit(
                    ClientEvent(
                        ClientEventKind.FETCHING,
                        message="Fetching and inspecting report (up to two minutes)...",
                    )
                )
                structure = inspect_report(session.fetch_report())
            except ZyBooksReportNotReady as error:
                emit(ClientEvent(ClientEventKind.NOT_READY, "Report not ready", str(error)))
                continue
            emit(
                ClientEvent(
                    ClientEventKind.COLUMNS,
                    "Report columns (not yet mapped to grades)",
                    columns=structure.columns,
                )
            )
            emit(
                ClientEvent(
                    ClientEventKind.SUMMARY,
                    "Structure captured",
                    f"{structure.record_count} records; {len(structure.columns)} columns.\n"
                    f"Captured in {monotonic() - started_at:.1f}s. Temporary download deleted.\n"
                    "Per-lab detail, current-best scores and completeness still need "
                    "verification.\n"
                    "Select another report in the same browser, or quit.",
                )
            )
    except (ZyBooksAccessError, ZyBooksReportError) as error:
        emit(ClientEvent(ClientEventKind.STOPPED, "Probe stopped", str(error)))
        return 1
    except (KeyboardInterrupt, EOFError):
        emit(
            ClientEvent(
                ClientEventKind.CANCELLED,
                message="Probe cancelled. No Canvas grades were changed.",
            )
        )
        return 0
    finally:
        session.close()


def run_retrieval(
    ask: Ask,
    emit: Emit,
    book_id: str,
    section_ids: tuple[int, ...],
    cutoff: datetime | None = None,
    session_factory: SessionFactory = ZyBooksBrowserSession,
) -> int:
    """One fetch handles navigation, scope, generation, job and file handling.

    Unlike :func:`run_probe`, a keyboard interrupt propagates (after the
    browser is closed) so the caller decides how a cancelled retrieval ends.
    """
    # Validate choices before opening a browser; capture 'now' after login below.
    RetrievalRequest(book_id, section_ids, cutoff or _current_minute())
    emit(
        ClientEvent(
            ClientEventKind.INTRODUCTION,
            "Automatic read-only retrieval",
            RETRIEVAL_INTRODUCTION,
        )
    )
    session = session_factory()
    try:
        emit(ClientEvent(ClientEventKind.OPENING_BROWSER, message="Opening zyBooks browser..."))
        session.__enter__()
        ask("After completing login, press Enter to retrieve: ")
        effective_cutoff = cutoff or _current_minute()
        request = RetrievalRequest(book_id, section_ids, effective_cutoff)
        started_at = monotonic()

        def report_progress(message: str) -> None:
            emit(ClientEvent(ClientEventKind.PROGRESS, message=message))

        report_progress("Retrieving report (up to two minutes)...")
        report = session.retrieve_report(request, report_progress)
        report_progress("Validating student and section coverage...")
        validated = validate_report(report)
        emit(
            ClientEvent(
                ClientEventKind.SUMMARY,
                "Report retrieved",
                f"Report {validated.report_id}: {validated.student_count} students; "
                f"{validated.section_count} sections.\n"
                f"Cutoff: {effective_cutoff.isoformat()}. "
                f"Retrieved in {monotonic() - started_at:.1f}s.\n"
                "Temporary download deleted. Roster and category coverage checked.\n"
                "CSV percentages retain source rounding. Exact earned points and "
                "attempt-policy validation are still required before best-five grading.",
            )
        )
        return 0
    finally:
        session.close()
