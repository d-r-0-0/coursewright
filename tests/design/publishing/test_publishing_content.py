"""Phase 5 publishing of modules and items: preview first, backup first.

COURSE_BUILDER had no tests for ``publish_content``; these are new.
"""

from __future__ import annotations

import pytest
from publishing_fakes import make_response

from coursewright.core.canvas.safe_write import BackupFailedError
from coursewright.design.publishing import content as publishing

COURSE_ID = 66
MODULES_PATH = rf"/api/v1/courses/{COURSE_ID}/modules"

MODULES = [
    {"id": 1, "name": "Start Here", "published": True},
    {"id": 2, "name": "Unit 01", "published": False},
    {"id": 3, "name": "Unit 02", "published": False},
]
ITEMS_BY_MODULE = {
    1: [{"id": 11, "title": "Welcome", "published": True}],
    2: [
        {"id": 21, "title": "Reading", "published": False},
        {"id": 22, "title": "Video", "published": True},
    ],
    3: [{"id": 31, "title": "Reading 2", "published": False}],
}


def serve_course(server, *, refused_item_ids=()):
    server.route("GET", MODULES_PATH, make_response(200, MODULES))
    for module in MODULES:
        module_path = MODULES_PATH + rf"/{module['id']}"
        server.route(
            "GET", module_path + "/items", make_response(200, ITEMS_BY_MODULE[module["id"]])
        )
        server.route("GET", module_path, make_response(200, module))
        server.route("PUT", module_path, make_response(200, {**module, "published": True}))
        for item in ITEMS_BY_MODULE[module["id"]]:
            item_path = module_path + rf"/items/{item['id']}"
            server.route("GET", item_path, make_response(200, item))
            status = 500 if item["id"] in refused_item_ids else 200
            server.route("PUT", item_path, make_response(status, item))


def test_preview_plans_without_writing_or_backing_up(server, client, tmp_path):
    serve_course(server)

    report = publishing.publish_course_content(client, COURSE_ID, tmp_path / "backups")

    assert report.executed is False
    assert report.modules_to_publish == 2
    assert report.items_to_publish == 2
    assert [planned.already_published_item_count for planned in report.modules] == [1, 1, 0]
    assert set(server.methods_sent) == {"GET"}
    assert not (tmp_path / "backups").exists()


def test_selected_modules_limit_the_plan_and_count_the_rest_as_skipped(server, client, tmp_path):
    serve_course(server)

    report = publishing.publish_course_content(
        client, COURSE_ID, tmp_path / "backups", module_names={"Unit 02"}
    )

    assert [planned.name for planned in report.modules] == ["Unit 02"]
    assert report.modules_skipped == 2


def test_execute_snapshots_the_modules_then_publishes_each_with_a_backup(server, client, tmp_path):
    serve_course(server)
    backup_directory = tmp_path / "backups"
    backups_at_each_put = []

    def note_backups_when_putting(method, url):
        if method == "PUT":
            backups_at_each_put.append(len(list(backup_directory.glob("*.json"))))

    server.before_request = note_backups_when_putting

    report = publishing.publish_course_content(client, COURSE_ID, backup_directory, execute=True)

    assert report.executed is True
    assert (report.modules_published, report.items_published) == (2, 2)
    assert report.errors == []
    assert server.methods_sent.count("PUT") == 4
    # The pre-publish snapshot plus one backup per write, each before its PUT.
    assert backups_at_each_put == [2, 3, 4, 5]
    assert len(report.backup_paths) == 5


def test_a_refused_item_is_reported_and_the_run_continues(server, client, tmp_path):
    serve_course(server, refused_item_ids={21})

    report = publishing.publish_course_content(
        client, COURSE_ID, tmp_path / "backups", execute=True
    )

    assert report.items_published == 1
    assert len(report.errors) == 1
    assert "Reading" in report.errors[0]


def test_snapshot_failure_aborts_before_anything_is_published(server, client, tmp_path):
    serve_course(server)
    blocked_directory = tmp_path / "not-a-directory"
    blocked_directory.write_text("a file where the backup folder should be")

    with pytest.raises(BackupFailedError):
        publishing.publish_course_content(client, COURSE_ID, blocked_directory, execute=True)

    assert "PUT" not in server.methods_sent


def test_progressive_release_picks_the_first_modules():
    assert publishing.first_module_names(MODULES, 2) == ["Start Here", "Unit 01"]
    with pytest.raises(ValueError, match="Invalid number"):
        publishing.first_module_names(MODULES, 0)
    with pytest.raises(ValueError, match="Invalid number"):
        publishing.first_module_names(MODULES, 4)


def test_the_checklist_is_available_for_the_ui():
    assert len(publishing.PRE_PUBLISH_CHECKLIST) == 6
