"""Offline safety checks for deleting announcements.

Ported from COURSE_BUILDER ``tests/test_delete_announcements.py``.  The
cantrip's ``main()`` is now :func:`delete_announcements`; its flags became
arguments.  Every test counts the HTTP requests the real client sent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from publishing_fakes import BASE_URL, make_response

from coursewright.core.canvas.client import CanvasAuthError
from coursewright.core.canvas.safe_write import BackupFailedError
from coursewright.design.publishing._strict_reads import CanvasReadError
from coursewright.design.publishing.deletions import (
    PartialDeletionError,
    delete_announcements,
)

COURSE_ID = 123
LIST_PATH = rf"/api/v1/courses/{COURSE_ID}/discussion_topics"
TOPIC_PATH = rf"/api/v1/courses/{COURSE_ID}/discussion_topics/\d+"

THREE_ANNOUNCEMENTS = [
    {"id": 1, "title": "A"},
    {"id": 2, "title": "B"},
    {"id": 3, "title": "C"},
]


def serve_announcements(server, pages=None, delete_responses=None):
    """Route the list, the per-topic read and the DELETE calls."""
    server.route("GET", LIST_PATH, pages or [make_response(200, THREE_ANNOUNCEMENTS)])
    server.route(
        "GET",
        TOPIC_PATH,
        lambda url, kwargs: make_response(200, {"id": int(url.rsplit("/", 1)[1]), "title": "x"}),
    )
    server.route("DELETE", TOPIC_PATH, delete_responses or (lambda url, kwargs: make_response(200)))


def only_get_methods(server):
    return set(server.methods_sent) <= {"GET"}


def test_default_run_is_a_dry_run_that_lists_what_would_go(server, client, tmp_path):
    serve_announcements(server)
    backup_directory = tmp_path / "backups"

    report = delete_announcements(client, COURSE_ID, backup_directory)

    assert report.executed is False
    assert [target.resource_id for target in report.will_delete] == [1, 2, 3]
    assert [target.label for target in report.will_delete] == ["A", "B", "C"]
    assert report.deleted == ()
    assert only_get_methods(server)
    assert server.methods_sent == ["GET"]
    assert not backup_directory.exists()


def test_execute_must_be_asked_for_by_name(server, client, tmp_path):
    """The historical ``--live`` flag is gone; ``execute`` is keyword-only."""
    with pytest.raises(TypeError):
        delete_announcements(client, COURSE_ID, tmp_path, True)  # type: ignore[misc]
    with pytest.raises(TypeError):
        delete_announcements(client, COURSE_ID, tmp_path, live=True)
    assert server.requests_sent == []


def test_execute_backs_up_the_whole_set_before_the_first_delete(server, client, tmp_path):
    serve_announcements(server)
    backup_directory = tmp_path / "backups"
    backups_seen_at_each_delete = []

    def record_backups_when_deleting(method, url):
        if method == "DELETE":
            backups_seen_at_each_delete.append(sorted(backup_directory.glob("*.json")))

    server.before_request = record_backups_when_deleting

    report = delete_announcements(client, COURSE_ID, backup_directory, execute=True)

    assert report.executed is True
    assert [target.resource_id for target in report.deleted] == [1, 2, 3]
    assert server.methods_sent.count("DELETE") == 3
    # At the first DELETE the full-set backup already existed on disk.
    first_delete_backups = backups_seen_at_each_delete[0]
    full_set = [path for path in first_delete_backups if "delete_announcements" in path.name]
    assert len(full_set) == 1
    saved = json.loads(full_set[0].read_text(encoding="utf-8"))
    assert {item["id"] for item in saved["state_before"]} == {1, 2, 3}
    assert full_set[0] in report.backup_paths


def test_every_page_is_fetched_and_backed_up(server, client, tmp_path):
    next_page_url = f"{BASE_URL}{LIST_PATH}?page=2"
    serve_announcements(
        server,
        pages=[
            make_response(
                200, THREE_ANNOUNCEMENTS[:2], link_header=f'<{next_page_url}>; rel="next"'
            ),
            make_response(200, THREE_ANNOUNCEMENTS[2:]),
        ],
    )

    report = delete_announcements(client, COURSE_ID, tmp_path / "backups", execute=True)

    assert [target.resource_id for target in report.will_delete] == [1, 2, 3]
    full_set_backup = next(
        Path(path) for path in report.backup_paths if "delete_announcements" in path.name
    )
    saved = json.loads(full_set_backup.read_text(encoding="utf-8"))
    assert [item["id"] for item in saved["state_before"]] == [1, 2, 3]
    assert server.methods_sent.count("DELETE") == 3


def test_backup_failure_prevents_every_delete(server, client, tmp_path, monkeypatch):
    serve_announcements(server)
    real_open = Path.open

    def open_that_fails_exclusive(self, mode="r", *args, **kwargs):
        if mode == "x":
            raise OSError("disk full")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_that_fails_exclusive)

    with pytest.raises(BackupFailedError, match="nothing was written"):
        delete_announcements(client, COURSE_ID, tmp_path / "backups", execute=True)

    assert "DELETE" not in server.methods_sent


def test_first_failed_delete_stops_and_reports_progress(server, client, tmp_path):
    serve_announcements(server, delete_responses=[make_response(500)])

    with pytest.raises(PartialDeletionError, match="after deleting 0 of 3") as stopped:
        delete_announcements(client, COURSE_ID, tmp_path / "backups", execute=True)

    assert server.methods_sent.count("DELETE") == 1
    assert stopped.value.report.deleted == ()
    assert [target.resource_id for target in stopped.value.report.will_delete] == [1, 2, 3]
    # The failed delete chains the writer's refusal, not a bare message.
    assert stopped.value.__cause__ is not None


def test_a_failure_after_some_deletes_names_what_was_already_removed(server, client, tmp_path):
    serve_announcements(
        server, delete_responses=[make_response(200), make_response(200), make_response(500)]
    )

    with pytest.raises(PartialDeletionError) as stopped:
        delete_announcements(client, COURSE_ID, tmp_path / "backups", execute=True)

    assert [target.resource_id for target in stopped.value.report.deleted] == [1, 2]
    assert server.methods_sent.count("DELETE") == 3


@pytest.mark.parametrize(
    ("failing_status", "expected_error", "message"),
    [(401, CanvasAuthError, "rejected authentication"), (500, CanvasReadError, "Failed to fetch")],
)
def test_failed_fetch_is_reported_not_treated_as_empty(
    server, client, tmp_path, failing_status, expected_error, message
):
    serve_announcements(server, pages=[make_response(failing_status, [])])

    with pytest.raises(expected_error, match=message):
        delete_announcements(client, COURSE_ID, tmp_path / "backups", execute=True)

    assert server.methods_sent == ["GET"]


def test_failed_second_page_writes_nothing(server, client, tmp_path):
    next_page_url = f"{BASE_URL}{LIST_PATH}?page=2"
    serve_announcements(
        server,
        pages=[
            make_response(
                200, THREE_ANNOUNCEMENTS[:2], link_header=f'<{next_page_url}>; rel="next"'
            ),
            make_response(500, []),
        ],
    )
    backup_directory = tmp_path / "backups"

    with pytest.raises(CanvasReadError):
        delete_announcements(client, COURSE_ID, backup_directory, execute=True)

    assert "DELETE" not in server.methods_sent
    assert not backup_directory.exists()


def test_no_response_is_reported_not_treated_as_empty(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client, "_make_request", lambda *args, **kwargs: None)

    with pytest.raises(CanvasReadError, match="no response"):
        delete_announcements(client, COURSE_ID, tmp_path, execute=True)

    assert not list(tmp_path.iterdir())


def test_empty_course_is_a_no_op_even_when_executing(server, client, tmp_path):
    serve_announcements(server, pages=[make_response(200, [])])
    backup_directory = tmp_path / "backups"

    report = delete_announcements(client, COURSE_ID, backup_directory, execute=True)

    assert report.will_delete == ()
    assert report.deleted == ()
    assert server.methods_sent == ["GET"]
    assert not backup_directory.exists()
