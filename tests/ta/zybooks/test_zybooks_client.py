"""Synthetic CSV and mocked workflow tests; no network or real student data.

The probe and retrieval workflows are silent library functions: they take an
``ask`` callable and an ``emit`` callable instead of a Rich console, so these
tests drive them with plain Python objects and read back the events.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from coursewright.ta.zybooks.client import (
    ClientEvent,
    ClientEventKind,
    run_probe,
    run_retrieval,
)
from coursewright.ta.zybooks.parser import ZyBooksReportError, inspect_report
from coursewright.ta.zybooks.session import ZyBooksAccessError, ZyBooksReportNotReady


def test_inspect_csv_preserves_headers_but_not_student_rows():
    report = (
        '﻿Name,Email,"Lab, one (10)"\r\n"Example, Student",private@example.invalid,80\r\n\r\n'
    ).encode()
    structure = inspect_report(report)
    assert structure.columns == ("Name", "Email", "Lab, one (10)")
    assert structure.record_count == 1
    assert "private@example.invalid" not in repr(structure)


def test_header_only_report_does_not_invent_records():
    assert inspect_report(b"Name,Email\n").record_count == 0


@pytest.mark.parametrize(
    "report",
    [
        b"",
        b"\xff",
        b"Name,Email\n\x00,test",
        b"<html>Login required</html>",
        b"Name,Name\n",
        b"Name, name\n",
        b"Name,\n",
        b"Name,Email\none\n",
        b'Name,Email\n"unterminated',
        b"Name,Email\na,b,c\n",
    ],
)
def test_unsupported_report_structure_is_rejected(report):
    with pytest.raises(ZyBooksReportError):
        inspect_report(report)


def test_report_size_is_bounded():
    with (
        patch("coursewright.ta.zybooks.parser.MAX_REPORT_BYTES", 4),
        pytest.raises(ZyBooksReportError, match="20 MiB"),
    ):
        inspect_report(b"Name,Email")


class Interaction:
    """Stand-in for ui/: scripted answers in, events out."""

    def __init__(self, answers=()):
        self.events: list[ClientEvent] = []
        self.prompts: list[str] = []
        self._answers = answers

    def ask(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self._answers, BaseException) or (
            isinstance(self._answers, type) and issubclass(self._answers, BaseException)
        ):
            raise self._answers
        if callable(self._answers):
            return self._answers(prompt)
        if isinstance(self._answers, str):
            return self._answers
        return next(self._answers)

    def emit(self, event):
        self.events.append(event)

    def text(self):
        """Every word of every event, for 'never shown' assertions."""
        return "\n".join(
            f"{event.title}\n{event.message}\n{' '.join(event.columns)}" for event in self.events
        )

    def kinds(self):
        return [event.kind for event in self.events]


@pytest.fixture
def probe_setup():
    session = MagicMock()
    session.fetch_report.return_value = b"Name,Email\nPrivate,private@example.invalid\n"
    return session, lambda: session


def run(interaction, session_factory, **kwargs):
    return run_probe(interaction.ask, interaction.emit, session_factory, **kwargs)


def test_probe_reuses_session_and_never_shows_student_rows(probe_setup):
    session, factory = probe_setup
    interaction = Interaction(iter(["fetch", "fetch", "quit"]))
    assert run(interaction, factory) == 0
    session.__enter__.assert_called_once()
    assert session.fetch_report.call_count == 2
    session.close.assert_called_once()
    assert "1 records; 2 columns" in interaction.text()
    assert "private@example.invalid" not in interaction.text()
    assert "Private" not in interaction.text()
    columns = [event for event in interaction.events if event.kind == ClientEventKind.COLUMNS]
    assert columns[0].columns == ("Name", "Email")


def test_quit_does_not_download(probe_setup):
    session, factory = probe_setup
    assert run(Interaction("quit"), factory) == 0
    session.fetch_report.assert_not_called()
    session.close.assert_called_once()


@pytest.mark.parametrize("failure", [KeyboardInterrupt, EOFError])
def test_cancel_closes_browser(probe_setup, failure):
    session, factory = probe_setup
    interaction = Interaction(failure)
    assert run(interaction, factory) == 0
    session.close.assert_called_once()
    assert interaction.kinds()[-1] == ClientEventKind.CANCELLED


def test_fetch_error_stops_without_retry(probe_setup):
    session, factory = probe_setup
    session.fetch_report.side_effect = ZyBooksAccessError("Login expired")
    interaction = Interaction("fetch")
    assert run(interaction, factory) == 1
    session.fetch_report.assert_called_once()
    session.close.assert_called_once()
    assert "Login expired" in interaction.text()
    assert interaction.kinds()[-1] == ClientEventKind.STOPPED


@pytest.mark.parametrize("next_answer", ["fetch", "quit"])
def test_not_ready_preserves_login_until_user_fetches_or_quits(probe_setup, next_answer):
    session, factory = probe_setup
    session.fetch_report.side_effect = [
        ZyBooksReportNotReady("Open your zyBook, then Reporting."),
        b"Name,Email\nExample,example@example.invalid\n",
    ]
    answers = iter(["fetch", next_answer, "quit"])

    def answer_after_navigation(_prompt):
        session.close.assert_not_called()
        return next(answers)

    interaction = Interaction(answer_after_navigation)
    assert run(interaction, factory) == 0
    session.__enter__.assert_called_once()
    assert session.fetch_report.call_count == (2 if next_answer == "fetch" else 1)
    session.close.assert_called_once()
    assert "Report not ready" in interaction.text()


def test_automatic_command_handles_navigation_and_files_without_more_prompts(probe_setup):
    session, factory = probe_setup
    cutoff = datetime(2026, 9, 8, tzinfo=UTC)
    validated = MagicMock(report_id=42, student_count=30, section_count=1)
    interaction = Interaction("")
    with patch("coursewright.ta.zybooks.client.validate_report", return_value=validated):
        code = run_retrieval(
            interaction.ask, interaction.emit, "ExampleBook", (12,), cutoff, factory
        )
    assert code == 0
    assert len(interaction.prompts) == 1
    session.retrieve_report.assert_called_once()
    assert session.retrieve_report.call_args.args[0].cutoff == cutoff
    assert "30 students" in interaction.text()
    assert "Exact earned points" in interaction.text()
    session.close.assert_called_once()


def test_retrieval_progress_reaches_the_interface(probe_setup):
    session, factory = probe_setup

    def retrieve(request, progress):
        progress("Opening Reporting and checking book metadata...")
        return MagicMock()

    session.retrieve_report.side_effect = retrieve
    validated = MagicMock(report_id=1, student_count=2, section_count=1)
    interaction = Interaction("")
    with patch("coursewright.ta.zybooks.client.validate_report", return_value=validated):
        run_retrieval(
            interaction.ask,
            interaction.emit,
            "ExampleBook",
            (12,),
            datetime(2026, 9, 8, tzinfo=UTC),
            factory,
        )
    progress_messages = [
        event.message for event in interaction.events if event.kind == ClientEventKind.PROGRESS
    ]
    assert "Opening Reporting and checking book metadata..." in progress_messages
    assert "Validating student and section coverage..." in progress_messages


def test_automatic_command_closes_browser_on_cancel(probe_setup):
    session, factory = probe_setup
    interaction = Interaction(KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        run_retrieval(interaction.ask, interaction.emit, "ExampleBook", (12,), None, factory)
    session.retrieve_report.assert_not_called()
    session.close.assert_called_once()


def test_invalid_scope_is_rejected_before_a_browser_opens(probe_setup):
    session, factory = probe_setup
    interaction = Interaction("")
    with pytest.raises(ValueError, match="zyBook ID"):
        run_retrieval(interaction.ask, interaction.emit, "../book", (12,), None, factory)
    session.__enter__.assert_not_called()
