"""Deleting a module or an assignment: refuse graded work, back up first.

Ported from COURSE_BUILDER ``tests/test_delete_module.py`` (the graded-item
rule) and extended with the dry-run and backup-first proofs that every delete
must carry.  ``delete_assignment`` had no source test; its tests are new.
"""

from __future__ import annotations

import json

import pytest
from publishing_fakes import make_response

from coursewright.core.canvas.safe_write import BackupFailedError
from coursewright.design.publishing.deletions import (
    DeletionRefusedError,
    DeletionTargetNotFoundError,
    PartialDeletionError,
    delete_assignment,
    delete_module,
    graded_module_items,
)

COURSE_ID = 11
MODULE_ID = 22
ASSIGNMENT_ID = 33
MODULE_PATH = rf"/api/v1/courses/{COURSE_ID}/modules/{MODULE_ID}"
ASSIGNMENT_PATH = rf"/api/v1/courses/{COURSE_ID}/assignments/{ASSIGNMENT_ID}"

SAFE_ITEMS = [{"id": 1, "type": "Page", "title": "Read me"}]


# ----- the graded-item rule (the source tests) -------------------------------


def test_pages_and_links_are_safe_to_unlink():
    items = [{"type": "Page"}, {"type": "ExternalUrl"}, {"type": "SubHeader"}]
    assert graded_module_items(items) == []


def test_assignments_quizzes_and_discussions_block_the_delete():
    items = [{"type": "Page"}, {"type": "Assignment"}, {"type": "Quiz"}, {"type": "Discussion"}]
    assert len(graded_module_items(items)) == 3


# ----- delete_module ---------------------------------------------------------


def serve_module(server, items, module_listing_after=None):
    # Canvas may omit embedded items: the backup must use the separate list.
    module = {"id": MODULE_ID, "name": "Duplicate help", "position": 2}
    server.route("GET", MODULE_PATH, make_response(200, module))
    server.route("GET", MODULE_PATH + "/items", make_response(200, items))
    server.route(
        "GET",
        rf"/api/v1/courses/{COURSE_ID}/modules",
        make_response(200, module_listing_after or []),
    )
    server.route("DELETE", MODULE_PATH, make_response(200, module))
    return module


def test_module_default_is_a_dry_run_naming_what_would_be_deleted(server, client, tmp_path):
    serve_module(server, SAFE_ITEMS)

    report = delete_module(client, COURSE_ID, MODULE_ID, tmp_path / "backups")

    assert report.executed is False
    assert [(t.kind, t.resource_id, t.label) for t in report.will_delete] == [
        ("module", MODULE_ID, "Duplicate help")
    ]
    assert report.deleted == ()
    assert "DELETE" not in server.methods_sent
    assert set(server.methods_sent) == {"GET"}
    assert not (tmp_path / "backups").exists()


def test_module_with_graded_items_is_refused_even_when_executing(server, client, tmp_path):
    serve_module(server, [*SAFE_ITEMS, {"id": 2, "type": "Assignment", "title": "Lab"}])

    with pytest.raises(DeletionRefusedError, match="1 graded item"):
        delete_module(client, COURSE_ID, MODULE_ID, tmp_path / "backups", execute=True)

    assert "DELETE" not in server.methods_sent
    assert not (tmp_path / "backups").exists()


def test_missing_module_is_reported(server, client, tmp_path):
    server.route("GET", MODULE_PATH, make_response(404))

    with pytest.raises(DeletionTargetNotFoundError):
        delete_module(client, COURSE_ID, MODULE_ID, tmp_path / "backups", execute=True)

    assert "DELETE" not in server.methods_sent


def test_module_execute_saves_the_module_and_items_before_the_delete(server, client, tmp_path):
    serve_module(server, SAFE_ITEMS)
    backup_directory = tmp_path / "backups"
    backups_present_at_delete = []

    def note_backups_when_deleting(method, url):
        if method == "DELETE":
            backups_present_at_delete.extend(backup_directory.glob("*.json"))

    server.before_request = note_backups_when_deleting

    report = delete_module(client, COURSE_ID, MODULE_ID, backup_directory, execute=True)

    assert report.executed is True
    assert report.verified_gone is True
    assert [t.resource_id for t in report.deleted] == [MODULE_ID]
    assert len(backups_present_at_delete) == 2
    snapshot = next(path for path in backups_present_at_delete if "snapshot" in path.name)
    saved = json.loads(snapshot.read_text(encoding="utf-8"))
    assert saved["state_before"]["module"]["id"] == MODULE_ID
    assert saved["state_before"]["items"] == SAFE_ITEMS
    assert set(report.backup_paths) == set(backups_present_at_delete)


def test_module_still_present_after_delete_is_flagged(server, client, tmp_path):
    serve_module(server, SAFE_ITEMS, module_listing_after=[{"id": MODULE_ID, "name": "x"}])

    report = delete_module(client, COURSE_ID, MODULE_ID, tmp_path / "backups", execute=True)

    assert report.verified_gone is False


def test_module_backup_failure_prevents_the_delete(server, client, tmp_path):
    serve_module(server, SAFE_ITEMS)
    blocked_directory = tmp_path / "not-a-directory"
    blocked_directory.write_text("a file where the backup folder should be")

    with pytest.raises(BackupFailedError):
        delete_module(client, COURSE_ID, MODULE_ID, blocked_directory, execute=True)

    assert "DELETE" not in server.methods_sent


def test_module_delete_refused_by_canvas_raises(server, client, tmp_path):
    serve_module(server, SAFE_ITEMS)
    server.routes.insert(0, ("DELETE", server.routes[-1][1], make_response(500)))

    with pytest.raises(PartialDeletionError, match="after deleting 0 of 1"):
        delete_module(client, COURSE_ID, MODULE_ID, tmp_path / "backups", execute=True)


# ----- delete_assignment -----------------------------------------------------


def serve_assignment(server, *, has_submissions=False, assignments_after=None):
    assignment = {
        "id": ASSIGNMENT_ID,
        "name": "Leftover lab",
        "points_possible": 10,
        "published": False,
        "has_submitted_submissions": has_submissions,
    }
    server.route("GET", ASSIGNMENT_PATH, make_response(200, assignment))
    server.route(
        "GET",
        rf"/api/v1/courses/{COURSE_ID}/assignments",
        make_response(200, assignments_after or []),
    )
    server.route("DELETE", ASSIGNMENT_PATH, make_response(200, assignment))
    return assignment


def test_assignment_default_is_a_dry_run_naming_what_would_be_deleted(server, client, tmp_path):
    serve_assignment(server)

    report = delete_assignment(client, COURSE_ID, ASSIGNMENT_ID, tmp_path / "backups")

    assert report.executed is False
    assert [(t.kind, t.resource_id, t.label) for t in report.will_delete] == [
        ("assignment", ASSIGNMENT_ID, "Leftover lab")
    ]
    assert set(server.methods_sent) == {"GET"}
    assert not (tmp_path / "backups").exists()


def test_assignment_with_submissions_is_refused_even_when_executing(server, client, tmp_path):
    serve_assignment(server, has_submissions=True)

    with pytest.raises(DeletionRefusedError, match="students have submitted"):
        delete_assignment(client, COURSE_ID, ASSIGNMENT_ID, tmp_path / "backups", execute=True)

    assert "DELETE" not in server.methods_sent
    assert not (tmp_path / "backups").exists()


def test_missing_assignment_is_reported(server, client, tmp_path):
    server.route("GET", ASSIGNMENT_PATH, make_response(404))

    with pytest.raises(DeletionTargetNotFoundError):
        delete_assignment(client, COURSE_ID, ASSIGNMENT_ID, tmp_path / "backups", execute=True)

    assert "DELETE" not in server.methods_sent


def test_assignment_execute_saves_it_before_the_delete_and_verifies(server, client, tmp_path):
    serve_assignment(server)
    backup_directory = tmp_path / "backups"
    backups_present_at_delete = []

    def note_backups_when_deleting(method, url):
        if method == "DELETE":
            backups_present_at_delete.extend(backup_directory.glob("*delete_assignment.json"))

    server.before_request = note_backups_when_deleting

    report = delete_assignment(client, COURSE_ID, ASSIGNMENT_ID, backup_directory, execute=True)

    assert report.executed is True
    assert report.verified_gone is True
    assert len(backups_present_at_delete) == 1
    saved = json.loads(backups_present_at_delete[0].read_text(encoding="utf-8"))
    assert saved["state_before"]["name"] == "Leftover lab"


def test_assignment_backup_failure_prevents_the_delete(server, client, tmp_path):
    serve_assignment(server)
    blocked_directory = tmp_path / "not-a-directory"
    blocked_directory.write_text("a file where the backup folder should be")

    with pytest.raises(BackupFailedError):
        delete_assignment(client, COURSE_ID, ASSIGNMENT_ID, blocked_directory, execute=True)

    assert "DELETE" not in server.methods_sent


def test_assignment_still_present_after_delete_is_flagged(server, client, tmp_path):
    serve_assignment(server, assignments_after=[{"id": ASSIGNMENT_ID, "name": "Leftover lab"}])

    report = delete_assignment(client, COURSE_ID, ASSIGNMENT_ID, tmp_path / "backups", execute=True)

    assert report.verified_gone is False
