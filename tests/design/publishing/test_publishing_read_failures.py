"""Incomplete reads must never authorize writes or certify a deletion."""

import json

import pytest
from publishing_fakes import BASE_URL, make_response

from coursewright.design.publishing._strict_reads import CanvasReadError
from coursewright.design.publishing.content import publish_course_content
from coursewright.design.publishing.copy_course import copy_course_content
from coursewright.design.publishing.deletions import (
    DeletionRefusedError,
    PartialDeletionError,
    delete_assignment,
    delete_module,
)


@pytest.mark.parametrize("failure", [make_response(500), make_response(200, {})])
def test_module_item_read_failure_prevents_deletion(server, client, tmp_path, failure):
    server.route("GET", r"/api/v1/courses/1/modules/2", make_response(200, {"id": 2}))
    server.route("GET", r"/api/v1/courses/1/modules/2/items", failure)

    with pytest.raises(CanvasReadError):
        delete_module(client, 1, 2, tmp_path, execute=True)

    assert set(server.methods_sent) == {"GET"}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("graded", [False, True])
def test_module_checks_and_backs_up_all_item_pages(server, client, tmp_path, graded):
    first = {"id": 3, "type": "Page"}
    second = {"id": 4, "type": "Assignment" if graded else "Page"}
    server.route("GET", r"/api/v1/courses/1/modules/2", make_response(200, {"id": 2}))
    server.route(
        "GET", r"/api/v1/courses/1/modules/2/items",
        make_response(200, [first], f'<{BASE_URL}/next>; rel="next"'),
    )
    server.route("GET", r"/next", make_response(200, [second]))
    server.route("GET", r"/api/v1/courses/1/modules", make_response(200, []))
    server.route("DELETE", r"/api/v1/courses/1/modules/2", make_response(200, {}))

    if graded:
        with pytest.raises(DeletionRefusedError):
            delete_module(client, 1, 2, tmp_path, execute=True)
        assert "DELETE" not in server.methods_sent
    else:
        def assert_backup(method, url):
            if method == "DELETE":
                path = next(tmp_path.glob("*snapshot.json"))
                assert json.loads(path.read_text())["state_before"]["items"] == [first, second]

        server.before_request = assert_backup
        assert delete_module(client, 1, 2, tmp_path, execute=True).verified_gone


@pytest.mark.parametrize("resource", ["modules", "assignments"])
def test_failed_readback_preserves_the_deletion_report(server, client, tmp_path, resource):
    server.route("GET", rf"/api/v1/courses/1/{resource}/2", make_response(200, {"id": 2}))
    server.route("GET", r"/api/v1/courses/1/modules/2/items", make_response(200, []))
    server.route("DELETE", rf"/api/v1/courses/1/{resource}/2", make_response(200, {}))
    server.route("GET", rf"/api/v1/courses/1/{resource}", make_response(500))
    delete = delete_module if resource == "modules" else delete_assignment

    with pytest.raises(PartialDeletionError, match="verification failed") as caught:
        delete(client, 1, 2, tmp_path, execute=True)

    assert [target.resource_id for target in caught.value.report.deleted] == [2]
    assert caught.value.report.verified_gone is None
    assert all(path.exists() for path in caught.value.report.backup_paths)


@pytest.mark.parametrize("resource", ["modules", "assignments"])
def test_failed_target_snapshot_never_starts_copy(server, client, tmp_path, resource):
    for course_id in (1, 2):
        prefix = rf"/api/v1/courses/{course_id}"
        server.route("GET", prefix, make_response(200, {"id": course_id}))
        modules = [{"id": 3}, {"id": 4}] if course_id == 1 else []
        for collection, payload in (("modules", modules), ("assignments", [])):
            status = 500 if course_id == 2 and collection == resource else 200
            server.route("GET", prefix + f"/{collection}", make_response(status, payload))
        server.route("GET", prefix + r"/modules/\d+/items", make_response(200, []))

    with pytest.raises(CanvasReadError):
        copy_course_content(client, 1, 2, tmp_path, execute=True)

    assert set(server.methods_sent) == {"GET"}
    assert not list(tmp_path.iterdir())


def test_failed_publishing_plan_writes_nothing(server, client, tmp_path):
    server.route("GET", r"/api/v1/courses/1/modules", make_response(500))

    with pytest.raises(CanvasReadError):
        publish_course_content(client, 1, tmp_path, execute=True)

    assert server.methods_sent == ["GET"]
    assert not list(tmp_path.iterdir())
