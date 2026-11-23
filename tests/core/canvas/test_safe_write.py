"""Safe Canvas writes: dry run by default, backup first, no silent failure.

These tests prove acceptance items 5 and 6 of the card: a write method
called without ``execute`` makes no HTTP write, and a write with ``execute``
saves its backup first and aborts if the backup cannot be saved.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import Mock

import pytest
import requests

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.dates import (
    format_canvas_datetime,
    validate_canvas_date_fields,
)
from coursewright.core.canvas.safe_write import (
    BackupFailedError,
    SafeCanvasWriter,
    SafeWriteError,
    WriteFailedError,
    create_safe_canvas_writer,
    default_backup_directory,
)

FIXED_MOMENT = datetime(2026, 3, 4, 5, 6, 7, 8)


def make_response(status_code, payload=None):
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(payload if payload is not None else {}).encode()
    return response


@pytest.fixture
def session():
    """A stand-in for the client's one ``requests.Session``."""
    return Mock()


@pytest.fixture
def client(session):
    canvas = CanvasClient("https://canvas.example.edu", "synthetic-token")
    canvas._session = session
    canvas._rate_limit_delay = 0
    return canvas


def make_writer(client, tmp_path, *, execute):
    return SafeCanvasWriter(
        client, tmp_path / "backups", execute=execute, clock=lambda: FIXED_MOMENT
    )


def http_methods_sent(session):
    return [call.args[0] for call in session.request.call_args_list]


# Every write the writer offers, as (method name, arguments).  One list
# drives the dry-run proof so a new write cannot ship without it.
ALL_WRITES = [
    ("update_course", (1, {"name": "X"})),
    ("update_module", (1, 2, {"published": True})),
    ("update_module_item", (1, 2, 3, {"position": 1})),
    ("move_module_item", (1, 2, 3, 4, 1)),
    ("reorder_modules", (1, [3, 2, 1])),
    ("update_assignment", (1, 5, {"due_at": "2026-05-01T23:59:00"})),
    ("update_assignment_group", (1, 6, {"group_weight": 10})),
    ("update_quiz", (1, 7, {"published": False})),
    ("update_discussion_topic", (1, 8, {"delayed_post_at": None})),
    ("update_page", (1, "intro", {"body": "<p>x</p>"})),
    ("set_course_nickname", (1, "Nick")),
    ("submit_grade", (1, 5, 11, 90.0, "Good work")),
    ("post_submission_comment", (1, 5, 11, "Please submit")),
    ("set_assignment_dates", (1, 5)),  # needs a date; see test below
    ("create_module", (1, {"name": "M"})),
    ("create_module_item", (1, 2, {"type": "Page"})),
    ("create_assignment", (1, {"name": "A"})),
    ("create_assignment_group", (1, {"name": "G"})),
    ("create_quiz", (1, {"title": "Q"})),
    ("create_quiz_question", (1, 7, {"question_text": "?"})),
    ("create_page", (1, {"title": "P"})),
    ("create_discussion_topic", (1, {"title": "T"})),
    ("post_announcement", (1, "Hello", "<p>Hi</p>")),
    ("post_discussion_reply", (1, 8, 9, "<p>Hi</p>")),
    ("post_conversation_message", (None, 11, "Subj", "Body")),
    ("copy_course_content", (2, 1)),
    ("delete_module", (1, 2)),
    ("delete_module_item", (1, 2, 3)),
    ("delete_assignment", (1, 5)),
    ("delete_assignment_group", (1, 6)),
    ("delete_discussion_topic", (1, 8)),
    ("delete_page", (1, "intro")),
]


@pytest.mark.parametrize(
    ("method_name", "arguments"),
    [pair for pair in ALL_WRITES if pair[0] != "set_assignment_dates"],
)
def test_dry_run_is_the_default_and_sends_no_http(
    client, session, tmp_path, method_name, arguments
):
    writer = SafeCanvasWriter(client, tmp_path / "backups")  # no execute argument

    outcome = getattr(writer, method_name)(*arguments)

    assert outcome.executed is False
    assert outcome.operation == method_name
    assert outcome.backup_path is None
    session.request.assert_not_called()  # not even a read
    assert not (tmp_path / "backups").exists()  # and no backup file


def test_dry_run_of_dates_and_publishing_sends_no_http(client, session, tmp_path):
    writer = make_writer(client, tmp_path, execute=False)

    dated = writer.set_assignment_dates(1, 5, due_at="2026-05-01T23:59:00")
    published = writer.set_published("module_item", 1, 3, True, module_id=2)

    assert dated.payload == {"due_at": "2026-05-01T23:59:00"}
    assert published.payload == {"published": True}
    session.request.assert_not_called()


def test_upload_dry_run_sends_no_http(client, session, tmp_path):
    local_file = tmp_path / "deck.pdf"
    local_file.write_bytes(b"x")

    outcome = make_writer(client, tmp_path, execute=False).upload_file(1, local_file, "Unit 1")

    assert outcome.executed is False
    assert outcome.payload == {"file_name": "deck.pdf", "folder": "Unit 1"}
    session.request.assert_not_called()


def test_dry_run_still_rejects_a_utc_date(client, session, tmp_path):
    """The preview must fail the way the real run would (rule 3.10)."""
    writer = make_writer(client, tmp_path, execute=False)

    with pytest.raises(ValueError, match="due_at"):
        writer.update_assignment(1, 5, {"due_at": "2026-05-01T23:59:00Z"})


def test_execute_writes_backup_before_the_put(client, session, tmp_path):
    order = []
    original_before = {"id": 5, "name": "Old", "due_at": "2026-04-01T23:59:00"}

    def fake_request(method, url, **kwargs):
        backups = list((tmp_path / "backups").glob("*.json"))
        order.append((method, len(backups)))
        if method == "GET":
            return make_response(200, original_before)
        return make_response(200, {"id": 5, "name": "New"})

    session.request.side_effect = fake_request
    writer = make_writer(client, tmp_path, execute=True)

    outcome = writer.update_assignment(1, 5, {"name": "New"})

    assert outcome.executed is True
    assert outcome.result == {"id": 5, "name": "New"}
    # GET sees no backup yet; the PUT sees exactly one already on disk.
    assert order == [("GET", 0), ("PUT", 1)]
    backup = json.loads(outcome.backup_path.read_text(encoding="utf-8"))
    assert backup["operation"] == "update_assignment"
    assert backup["target"] == "course 1 assignment 5"
    assert backup["payload"] == {"name": "New"}
    assert backup["state_before"] == original_before
    assert outcome.backup_path.name == "20260304-050607-000008_update_assignment.json"


def test_execute_aborts_without_writing_when_backup_cannot_be_saved(client, session, tmp_path):
    blocker = tmp_path / "backups"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    session.request.return_value = make_response(200, {"id": 5})
    writer = SafeCanvasWriter(client, blocker, execute=True)

    with pytest.raises(BackupFailedError, match="nothing was written"):
        writer.update_assignment(1, 5, {"name": "New"})

    assert http_methods_sent(session) == ["GET"]  # the read only; no PUT


def test_execute_aborts_when_current_state_cannot_be_read(client, session, tmp_path):
    session.request.return_value = make_response(404, {})
    writer = make_writer(client, tmp_path, execute=True)

    with pytest.raises(BackupFailedError, match="current state"):
        writer.delete_page(1, "missing")

    assert http_methods_sent(session) == ["GET"]
    assert not (tmp_path / "backups").exists()


def test_execute_aborts_when_payload_cannot_be_serialised(client, session, tmp_path):
    writer = make_writer(client, tmp_path, execute=True)

    with pytest.raises(BackupFailedError):
        writer.create_page(1, {"title": "P", "body": object()})

    session.request.assert_not_called()


def test_backup_failure_chains_the_original_error(client, tmp_path):
    blocker = tmp_path / "backups"
    blocker.write_text("x", encoding="utf-8")

    with pytest.raises(BackupFailedError) as raised:
        SafeCanvasWriter(client, blocker, execute=True).create_page(1, {"title": "P"})

    assert isinstance(raised.value.__cause__, OSError)


def test_create_execute_backs_up_payload_then_posts(client, session, tmp_path):
    session.request.return_value = make_response(201, {"id": 11, "title": "P"})
    writer = make_writer(client, tmp_path, execute=True)

    outcome = writer.create_page(1, {"title": "P"})

    assert http_methods_sent(session) == ["POST"]
    backup = json.loads(outcome.backup_path.read_text(encoding="utf-8"))
    assert backup["payload"] == {"title": "P"}
    assert backup["state_before"] is None


def test_refused_write_raises_and_keeps_the_backup(client, session, tmp_path):
    session.request.side_effect = [
        make_response(200, {"id": 5}),
        make_response(422, {"errors": ["bad"]}),
    ]
    writer = make_writer(client, tmp_path, execute=True)

    with pytest.raises(WriteFailedError, match="backup kept at"):
        writer.update_assignment(1, 5, {"name": "New"})

    assert len(list((tmp_path / "backups").glob("*.json"))) == 1


def test_failed_delete_raises_instead_of_returning_false(client, session, tmp_path):
    session.request.side_effect = [
        make_response(200, {"url": "intro", "body": "<p>keep me</p>"}),
        make_response(500, {}),
    ]

    with pytest.raises(WriteFailedError):
        make_writer(client, tmp_path, execute=True).delete_page(1, "intro")


def test_network_failure_on_write_raises(client, session, tmp_path):
    session.request.side_effect = [
        make_response(200, {"id": 5}),
        requests.exceptions.ConnectionError("down"),
    ]

    with pytest.raises(WriteFailedError):
        make_writer(client, tmp_path, execute=True).update_assignment(1, 5, {"name": "N"})


def test_delete_backup_keeps_what_was_removed(client, session, tmp_path):
    session.request.side_effect = [
        make_response(200, {"url": "intro", "body": "<p>keep me</p>"}),
        make_response(200, {}),
    ]

    outcome = make_writer(client, tmp_path, execute=True).delete_page(1, "intro")

    backup = json.loads(outcome.backup_path.read_text(encoding="utf-8"))
    assert backup["state_before"]["body"] == "<p>keep me</p>"
    assert http_methods_sent(session) == ["GET", "DELETE"]


def test_set_published_routes_each_kind_to_its_update(client, session, tmp_path):
    session.request.return_value = make_response(200, {"id": 1})
    writer = make_writer(client, tmp_path, execute=True)

    writer.set_published("page", 1, "intro", False)
    put_urls = [c.args[1] for c in session.request.call_args_list if c.args[0] == "PUT"]

    assert put_urls[-1].endswith("/courses/1/pages/intro")
    with pytest.raises(ValueError, match="module_id"):
        writer.set_published("module_item", 1, 3, True)
    with pytest.raises(ValueError, match="Cannot publish"):
        writer.set_published("course", 1, 1, True)


def test_set_assignment_dates_needs_a_date(client, tmp_path):
    with pytest.raises(ValueError, match="at least one date"):
        make_writer(client, tmp_path, execute=False).set_assignment_dates(1, 5)


def test_upload_execute_backs_up_before_reserving_upload(client, session, tmp_path):
    local_file = tmp_path / "deck.pdf"
    local_file.write_bytes(b"x")
    session.request.side_effect = [
        make_response(200, {"upload_url": "https://files.example.net/up"}),
        make_response(201, {"id": 9}),
    ]

    outcome = make_writer(client, tmp_path, execute=True).upload_file(1, local_file)

    assert outcome.backup_path.exists()
    assert outcome.result == {"id": 9}


def test_factory_places_backups_in_the_private_workspace(client, tmp_path):
    writer = create_safe_canvas_writer(client, workspace_directory=tmp_path)

    assert writer.execute is False
    assert writer._backup_directory == default_backup_directory(tmp_path)
    assert default_backup_directory(tmp_path) == tmp_path / "backups" / "canvas"


def test_factory_reads_workspace_from_environment(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))

    writer = create_safe_canvas_writer(client, execute=True)

    assert writer.execute is True
    assert writer._backup_directory == tmp_path / "backups" / "canvas"


def test_factory_refuses_to_run_without_a_workspace(client, monkeypatch, tmp_path):
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)  # no config file here either

    with pytest.raises(SafeWriteError, match="COURSEWRIGHT_WORKSPACE"):
        create_safe_canvas_writer(client)


# ---------------------------------------------------------------------
# Canvas dates carry no timezone suffix (rule 3.10, ADR-0101).
# ---------------------------------------------------------------------


def test_format_canvas_datetime_is_naive_without_z():
    assert format_canvas_datetime(datetime(2025, 9, 5, 23, 59)) == "2025-09-05T23:59:00"


def test_format_canvas_datetime_rejects_aware_datetimes():
    aware = datetime(2025, 9, 5, 23, 59, tzinfo=datetime.now().astimezone().tzinfo)

    with pytest.raises(ValueError, match="naive"):
        format_canvas_datetime(aware)


@pytest.mark.parametrize(
    "bad_value",
    [
        "2025-09-05T23:59:00Z",
        "2025-09-05T23:59:00-04:00",
        "2025-09-05",
        "2025-09-05 23:59:00",
        datetime(2025, 9, 5, 23, 59),
        20250905,
    ],
)
def test_validate_rejects_misformatted_dates(bad_value):
    with pytest.raises(ValueError, match="due_at"):
        validate_canvas_date_fields({"assignment": {"due_at": bad_value}})


def test_validate_accepts_clean_and_cleared_dates_and_walks_nesting():
    validate_canvas_date_fields({"due_at": "2025-09-05T23:59:00", "lock_at": None})
    validate_canvas_date_fields({"overrides": [{"unlock_at": "2025-09-01T00:00:00"}]})
    with pytest.raises(ValueError, match="unlock_at"):
        validate_canvas_date_fields({"overrides": [{"unlock_at": "2025-09-01T00:00:00Z"}]})


def test_submit_grade_backs_up_the_submission_then_puts_the_grade(client, session, tmp_path):
    prior_submission = {"id": 3, "user_id": 11, "score": 40.0}

    def fake_request(method, url, **kwargs):
        if method == "GET":
            return make_response(200, prior_submission)
        return make_response(200, {"id": 3, "score": 90.0})

    session.request.side_effect = fake_request
    writer = make_writer(client, tmp_path, execute=True)

    outcome = writer.submit_grade(1, 5, 11, 90.0, "Good work")

    assert http_methods_sent(session) == ["GET", "PUT"]
    assert outcome.executed is True
    backup = json.loads(outcome.backup_path.read_text(encoding="utf-8"))
    assert backup["payload"] == {"score": 90.0, "comment": "Good work"}
    assert backup["state_before"] == prior_submission


def test_submission_comment_backs_up_the_submission_then_puts_the_comment(
    client, session, tmp_path
):
    prior_submission = {"id": 3, "user_id": 11, "score": None}

    def fake_request(method, url, **kwargs):
        if method == "GET":
            return make_response(200, prior_submission)
        return make_response(200, {"id": 3})

    session.request.side_effect = fake_request
    writer = make_writer(client, tmp_path, execute=True)

    outcome = writer.post_submission_comment(1, 5, 11, "Please submit")

    assert http_methods_sent(session) == ["GET", "PUT"]
    put_call = session.request.call_args_list[1]
    assert put_call.kwargs["data"] == {"comment[text_comment]": "Please submit"}
    backup = json.loads(outcome.backup_path.read_text(encoding="utf-8"))
    assert backup["payload"] == {"comment": "Please submit"}
    assert backup["state_before"] == prior_submission


def test_submission_comment_refused_by_canvas_raises(client, session, tmp_path):
    def fake_request(method, url, **kwargs):
        return make_response(200 if method == "GET" else 422, {"id": 3})

    session.request.side_effect = fake_request
    writer = make_writer(client, tmp_path, execute=True)

    with pytest.raises(WriteFailedError, match="backup kept"):
        writer.post_submission_comment(1, 5, 11, "Please submit")


def test_submit_grade_refused_by_canvas_raises(client, session, tmp_path):
    def fake_request(method, url, **kwargs):
        return make_response(200 if method == "GET" else 422, {"id": 3})

    session.request.side_effect = fake_request
    writer = make_writer(client, tmp_path, execute=True)

    with pytest.raises(WriteFailedError, match="backup kept"):
        writer.submit_grade(1, 5, 11, 90.0)
