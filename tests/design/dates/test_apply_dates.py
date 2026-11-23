"""Schedule parsing and date application (ported from test_apply_dates.py).

Everything here is synthetic: a made-up course, made-up ids and dates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import BackupFailedError, SafeCanvasWriter
from coursewright.core.config import settings
from coursewright.design.dates.apply import apply_dates_to_course, find_schedule_key
from coursewright.design.dates.schedule import parse_schedule_file, parse_schedule_text

COURSE_ID = 4242
SCHEDULE_TEXT = (
    "# unlock_hour: 8\n"
    "Unit\tAssigned\t1st Post\tDue\n"
    "Unit 01\t09/02/2030\t09/06/2030\t09/13/2030\n"
    "Unit 02\t09/09/2030\t\t09/20/2030\n"
)


def test_schedule_parser_keeps_units_without_discussions(tmp_path: Path) -> None:
    """A blank first-post column must not discard an otherwise valid unit."""
    schedule_path = tmp_path / "schedule.txt"
    schedule_path.write_text(
        "Unit 01\t08/26/2030\t08/30/2030\t09/06/2030\n"
        "Unit 02\t08/26/2030\t\t09/06/2030\n",
        encoding="utf-8",
    )

    schedule = parse_schedule_file(schedule_path)

    assert list(schedule) == ["Unit 01", "Unit 02"]
    assert schedule["Unit 01"]["first_post_at"] == "2030-08-30T12:00:00"
    assert schedule["Unit 02"]["first_post_at"] is None
    assert schedule["Unit 02"]["first_post_display"] == ""
    assert schedule["Unit 02"]["due_at"] == "2030-09-06T12:00:00"


def test_schedule_parser_uses_canvas_date_format(tmp_path: Path, monkeypatch) -> None:
    """Date formatting must route through the central Canvas configuration."""
    schedule_path = tmp_path / "schedule.txt"
    schedule_path.write_text("Unit 15\t10/07/2030\t\t10/14/2030\n", encoding="utf-8")
    monkeypatch.setattr(settings, "DATE_FORMAT_OUTPUT", "%Y/%m/%d %H:%M:%S")

    schedule = parse_schedule_file(schedule_path)

    assert schedule["Unit 15"]["unlock_at"] == "2030/10/07 00:00:00"
    assert schedule["Unit 15"]["due_at"] == "2030/10/14 12:00:00"


def test_schedule_parser_reports_bad_rows_as_warnings() -> None:
    warnings: list[str] = []

    schedule = parse_schedule_text(
        "Unit 01\t09/02/2030\t\n"  # too few columns
        "Unit 02\tnot-a-date\t\t09/13/2030\n"
        "Unit 03\t09/02/30\t\t09/13/30\n",  # two-digit years are accepted
        warnings,
    )

    assert list(schedule) == ["Unit 03"]
    assert len(warnings) == 2
    assert "expected 4 columns" in warnings[0]
    assert "Could not parse dates" in warnings[1]


def test_schedule_parser_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_schedule_file(tmp_path / "absent.txt")


def test_unlock_hour_directive_applies_to_rows_below_it() -> None:
    schedule = parse_schedule_text(
        "Unit 01\t09/02/2030\t\t09/13/2030\n"
        "# unlock_hour: 8\n"
        "Unit 02\t09/09/2030\t\t09/20/2030\n"
    )

    assert schedule["Unit 01"]["unlock_at"] == "2030-09-02T00:00:00"
    assert schedule["Unit 02"]["unlock_at"] == "2030-09-09T08:00:00"


def test_header_and_comments_are_skipped() -> None:
    assert list(parse_schedule_text(SCHEDULE_TEXT)) == ["Unit 01", "Unit 02"]


def test_module_matches_by_unit_prefix() -> None:
    schedule = parse_schedule_text(SCHEDULE_TEXT)

    assert find_schedule_key("Unit 01: Getting Started", schedule) == "Unit 01"
    assert find_schedule_key("Welcome", schedule) is None


# ----- applying dates to a fake course ------------------------------------


@pytest.fixture
def client() -> Mock:
    """A synthetic course: two unit modules and an unscheduled one."""
    fake = Mock(spec=CanvasClient)
    fake.get_modules.return_value = [
        {"id": 1, "name": "Unit 01: Getting Started"},
        {"id": 2, "name": "Unit 02: Next Steps"},
        {"id": 3, "name": "Resources"},
    ]
    items_by_module = {
        1: [
            {"type": "Assignment", "title": "Worksheet", "content_id": 11},
            {"type": "Quiz", "title": "Check", "content_id": 12},
            {"type": "Discussion", "title": "Introductions", "content_id": 500},
            {"type": "Page", "title": "Reading", "content_id": 13},
            {"type": "ExternalUrl", "title": "Link"},
        ],
        2: [{"type": "Assignment", "title": "Lab", "content_id": 21}],
    }
    fake.get_module_items.side_effect = lambda _course, module_id: items_by_module[module_id]
    fake.get_discussion_topics.return_value = [
        {"id": 500, "assignment_id": 600},
        {"id": 501, "assignment_id": None},
    ]
    return fake


def make_writer(client: Mock, tmp_path: Path, *, execute: bool) -> SafeCanvasWriter:
    return SafeCanvasWriter(client, tmp_path / "backups", execute=execute)


def accept_every_write(client: Mock) -> None:
    """Make the fake Canvas return a record for every read and write."""
    for name in ("get_module", "get_assignment", "get_quiz"):
        getattr(client, name).return_value = {"id": 1}
    for name in ("update_module", "update_assignment", "update_quiz"):
        getattr(client, name).return_value = {"id": 1}


def test_dry_run_reports_changes_and_writes_nothing(client: Mock, tmp_path: Path) -> None:
    schedule = parse_schedule_text(SCHEDULE_TEXT)
    writer = make_writer(client, tmp_path, execute=False)

    report = apply_dates_to_course(client, writer, COURSE_ID, schedule)

    assert (report.modules_updated, report.assignments_updated) == (2, 2)
    assert (report.quizzes_updated, report.discussions_updated) == (1, 1)
    assert (report.modules_skipped, report.skipped_module_names) == (1, ["Resources"])
    assert all(not change.applied for change in report.changes)
    client.update_module.assert_not_called()
    client.update_assignment.assert_not_called()
    client.update_quiz.assert_not_called()
    assert not (tmp_path / "backups").exists()


def test_execute_writes_dates_through_the_safe_writer(client: Mock, tmp_path: Path) -> None:
    schedule = parse_schedule_text(SCHEDULE_TEXT)
    accept_every_write(client)
    writer = make_writer(client, tmp_path, execute=True)

    report = apply_dates_to_course(client, writer, COURSE_ID, schedule)

    assert report.errors == []
    dates = {"due_at": "2030-09-13T12:00:00", "unlock_at": "2030-09-02T08:00:00"}
    client.update_module.assert_any_call(COURSE_ID, 1, {"unlock_at": "2030-09-02T08:00:00"})
    client.update_quiz.assert_called_once_with(COURSE_ID, 12, dates)
    # The discussion's dates go to its assignment (600), not the topic id (500).
    client.update_assignment.assert_any_call(COURSE_ID, 600, dates)
    client.update_assignment.assert_any_call(COURSE_ID, 11, dates)
    assert all(change.applied for change in report.changes)
    # A backup exists for every object written: 2 modules, 3 assignments, 1 quiz.
    assert len(list((tmp_path / "backups").glob("*.json"))) == 6


def test_unknown_discussion_topic_is_recorded_not_raised(client: Mock, tmp_path: Path) -> None:
    schedule = parse_schedule_text(SCHEDULE_TEXT)
    client.get_discussion_topics.return_value = []
    writer = make_writer(client, tmp_path, execute=False)

    report = apply_dates_to_course(client, writer, COURSE_ID, schedule)

    assert report.discussions_updated == 0
    assert len(report.errors) == 1
    assert "Introductions" in report.errors[0]


def test_refused_write_is_an_error_entry_and_the_rest_continue(
    client: Mock, tmp_path: Path
) -> None:
    schedule = parse_schedule_text(SCHEDULE_TEXT)
    accept_every_write(client)
    client.update_module.side_effect = [None, {"id": 2}]  # Canvas refuses unit 1
    writer = make_writer(client, tmp_path, execute=True)

    report = apply_dates_to_course(client, writer, COURSE_ID, schedule)

    assert report.modules_updated == 1
    assert len(report.errors) == 1
    assert "Unit 01" in report.errors[0]
    # Unit 02 still got its assignment.
    client.update_assignment.assert_called_once_with(
        COURSE_ID,
        21,
        {"due_at": "2030-09-20T12:00:00", "unlock_at": "2030-09-09T08:00:00"},
    )


def test_failed_backup_aborts_instead_of_continuing(client: Mock, tmp_path: Path) -> None:
    schedule = parse_schedule_text(SCHEDULE_TEXT)
    client.get_module.return_value = None  # nothing to back up
    writer = make_writer(client, tmp_path, execute=True)

    with pytest.raises(BackupFailedError):
        apply_dates_to_course(client, writer, COURSE_ID, schedule)

    client.update_module.assert_not_called()


def test_no_emitted_canvas_date_ends_in_z(client: Mock, tmp_path: Path) -> None:
    """Every date sent to Canvas, and every backup of one, is naive local time."""
    schedule = parse_schedule_text(SCHEDULE_TEXT)
    accept_every_write(client)
    writer = make_writer(client, tmp_path, execute=True)

    report = apply_dates_to_course(client, writer, COURSE_ID, schedule)

    sent_dates = [
        value
        for call in (
            client.update_module.call_args_list
            + client.update_assignment.call_args_list
            + client.update_quiz.call_args_list
        )
        for value in call.args[-1].values()
    ]
    assert sent_dates
    assert not any(value.endswith("Z") for value in sent_dates)
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", value) for value in sent_dates)

    reported = [value for change in report.changes for value in change.updates.values()]
    assert reported
    assert not any(value.endswith("Z") for value in reported)

    for entry in schedule.values():
        for key in ("unlock_at", "first_post_at", "due_at"):
            assert entry[key] is None or not entry[key].endswith("Z")
    for backup in (tmp_path / "backups").glob("*.json"):
        payload = json.loads(backup.read_text(encoding="utf-8"))["payload"]
        assert not any(str(value).endswith("Z") for value in payload.values())
