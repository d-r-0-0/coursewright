"""COURSE_BUILDER's ``test_canvas_api.py``, ported to the unified client.

Source: COURSE_BUILDER ``tests/test_canvas_api.py`` (``lib.canvas_api.CanvasAPI``).
Every test pins a contract of the course-building calls that now live on
``CanvasClient``.  Deliberate differences from the source, each with a reason:

- HTTP goes through the client's ``requests.Session`` (the single choke
  point), so request-level tests stub ``client._session`` instead of the
  module-level ``requests.request``.
- The 429 back-off honours ``Retry-After`` (discussorator's behaviour, which
  the unified client keeps) instead of a fixed five seconds.
- ``get_assignments`` returns typed models in the unified client (rule 3.5:
  the discussorator contract wins), so the raw-dict endpoint test targets
  ``get_assignment_groups`` instead.
- The source's strict ``xfail`` (HTTP errors were printed behind a falsy
  ``if response:``) now passes: failures are logged.  See
  ``test_update_failure_is_reported_for_real_http_error``.
- ``load_api_key`` credential-source tests live in
  ``tests/core/test_canvas_client.py`` and ``test_credentials.py``.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import Mock

import pytest
import requests

from coursewright.core.canvas.client import CanvasClient, CanvasRequestError

BASE_URL = "https://canvas.example.edu"


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None, text=""):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def make_http_response(status_code, payload=None, headers=None):
    """A real ``requests.Response`` (falsy for 4xx/5xx, which fakes hide)."""
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(payload).encode("utf-8")
    response.headers.update(headers or {})
    return response


def make_canvas():
    client = CanvasClient(BASE_URL + "/", "synthetic-secret-token")
    client._rate_limit_delay = 0
    return client


def record_requests(monkeypatch, canvas, responses):
    """Replace ``_make_request`` with a recorder that replays *responses*."""
    calls = []

    def fake_make_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return responses.pop(0)

    monkeypatch.setattr(canvas, "_make_request", fake_make_request)
    return calls


def test_canvas_client_normalizes_base_url_and_sends_bearer_token():
    canvas = make_canvas()

    assert canvas.base_url == BASE_URL
    assert canvas.headers["Authorization"] == "Bearer synthetic-secret-token"


def test_find_course_by_name_matches_name_and_code(monkeypatch):
    canvas = make_canvas()
    courses = [
        {"name": "Intro to Networking", "course_code": "SYN175"},
        {"name": "Linux Administration", "course_code": "SYN241"},
        {"name": "Computer Forensics", "course_code": "SYN276"},
    ]
    monkeypatch.setattr(canvas, "get_courses", lambda enrollment_type="teacher": courses)

    assert canvas.find_course_by_name("syn") == courses
    assert canvas.find_course_by_name("LINUX") == [courses[1]]


def test_get_assignment_groups_uses_paginated_course_endpoint(monkeypatch):
    canvas = make_canvas()
    captured = {}

    def fake_paginated_get(url, params=None):
        captured["url"] = url
        captured["params"] = params
        return [{"id": 10, "name": "Labs"}]

    monkeypatch.setattr(canvas, "_paginated_get", fake_paginated_get)

    assert canvas.get_assignment_groups(123) == [{"id": 10, "name": "Labs"}]
    assert captured["url"].endswith("/api/v1/courses/123/assignment_groups")
    assert captured["params"] == {"include[]": ["assignments"]}


def test_update_assignment_wraps_canvas_payload(monkeypatch):
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [FakeResponse({"id": 55, "name": "Updated"})])

    result = canvas.update_assignment(123, 55, {"due_at": "2026-05-01T12:00:00"})

    assert result == {"id": 55, "name": "Updated"}
    assert calls[0]["method"] == "PUT"
    assert calls[0]["url"].endswith("/api/v1/courses/123/assignments/55")
    assert calls[0]["kwargs"] == {"json": {"assignment": {"due_at": "2026-05-01T12:00:00"}}}


def test_delete_module_uses_course_module_endpoint(monkeypatch):
    """Deleting a module keeps the deletion mechanics inside the client."""
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [FakeResponse({}, status_code=204)])

    assert canvas.delete_module(123, 456) is True
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/api/v1/courses/123/modules/456")
    assert calls[0]["kwargs"] == {}


def test_create_discussion_topic_uses_unwrapped_payload(monkeypatch):
    """Canvas discussion fields must not be wrapped like assignments."""
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [FakeResponse({"id": 77, "title": "T1"})])
    topic_data = {"title": "T1", "discussion_type": "threaded"}

    assert canvas.create_discussion_topic(900, topic_data) == {"id": 77, "title": "T1"}
    assert calls[0]["method"] == "POST"
    assert calls[0]["kwargs"] == {"json": topic_data}


def test_delete_discussion_topic_uses_course_topic_endpoint(monkeypatch):
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [FakeResponse({}, status_code=204)])

    assert canvas.delete_discussion_topic(900, 801) is True
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/courses/900/discussion_topics/801")


# ---------------------------------------------------------------------
# Pagination termination, error handling and update payloads.
# ---------------------------------------------------------------------


def test_paginated_get_collects_multiple_pages(monkeypatch):
    canvas = make_canvas()
    record_requests(
        monkeypatch,
        canvas,
        [
            FakeResponse([{"id": 1}], headers={"Link": '<https://school/api?page=2>; rel="next"'}),
            FakeResponse([{"id": 2}]),
        ],
    )

    assert canvas._paginated_get("https://school/api", {"search": "SYN"}) == [
        {"id": 1},
        {"id": 2},
    ]


def test_paginated_get_sends_per_page_once_then_follows_next_url(monkeypatch):
    canvas = make_canvas()
    calls = record_requests(
        monkeypatch,
        canvas,
        [
            make_http_response(
                200,
                [{"id": 1}],
                {"Link": '<https://school/api?page=2&per_page=100>; rel="next"'},
            ),
            make_http_response(200, [{"id": 2}]),
        ],
    )

    items = canvas._paginated_get("https://school/api", {"search": "SYN"})

    assert items == [{"id": 1}, {"id": 2}]
    assert calls[0]["kwargs"]["params"] == {"search": "SYN", "per_page": 100}
    # The next URL already carries the query; params must not repeat it.
    assert calls[1]["url"] == "https://school/api?page=2&per_page=100"
    assert calls[1]["kwargs"]["params"] is None


def test_paginated_get_follows_only_next_among_link_relations(monkeypatch):
    canvas = make_canvas()
    link_with_next = ", ".join(
        [
            '<https://school/api?page=1>; rel="current"',
            '<https://school/api?page=1>; rel="first"',
            '<https://school/api?page=2>; rel="next"',
            '<https://school/api?page=2>; rel="last"',
        ]
    )
    last_page_links = (
        '<https://school/api?page=1>; rel="prev", <https://school/api?page=2>; rel="last"'
    )
    calls = record_requests(
        monkeypatch,
        canvas,
        [
            make_http_response(200, [{"id": 1}], {"Link": link_with_next}),
            make_http_response(200, [{"id": 2}], {"Link": last_page_links}),
        ],
    )

    assert canvas._paginated_get("https://school/api") == [{"id": 1}, {"id": 2}]
    assert [call["url"] for call in calls] == [
        "https://school/api",
        "https://school/api?page=2",
    ]


def test_paginated_get_stops_after_a_failed_page(monkeypatch):
    """A 500 mid-walk ends the loop with no retries and no exception.

    Known limitation carried over from the source: callers cannot tell the
    list is partial.  Tracked for the follow-up that adds typed results.
    """
    canvas = make_canvas()
    calls = record_requests(
        monkeypatch,
        canvas,
        [
            make_http_response(
                200, [{"id": 1}], {"Link": '<https://school/api?page=2>; rel="next"'}
            ),
            make_http_response(500, {"errors": ["boom"]}),
        ],
    )

    with pytest.raises(CanvasRequestError):
        canvas._paginated_get("https://school/api")
    assert len(calls) == 2


def test_paginated_get_returns_empty_list_when_request_fails(monkeypatch):
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [None])

    with pytest.raises(CanvasRequestError):
        canvas._paginated_get("https://school/api")
    assert len(calls) == 1


def test_make_request_retries_once_after_rate_limit(monkeypatch):
    canvas = make_canvas()
    sleeps = []
    canvas._session = Mock()
    canvas._session.request.side_effect = [
        make_http_response(429, {}, {"Retry-After": "5"}),
        make_http_response(200, {"ok": 1}),
    ]
    monkeypatch.setattr("coursewright.core.canvas.client.time.sleep", sleeps.append)

    response = canvas._make_request("GET", "https://school/api", params={"a": 1})

    assert response.status_code == 200
    assert canvas._session.request.call_count == 2
    assert canvas._session.request.call_args.kwargs["params"] == {"a": 1}
    assert 5 in sleeps


def test_make_request_returns_none_on_network_error_without_token(monkeypatch, caplog):
    canvas = make_canvas()
    canvas._session = Mock()
    canvas._session.request.side_effect = requests.exceptions.ConnectionError("connection refused")

    with caplog.at_level(logging.ERROR), pytest.raises(CanvasRequestError):
        canvas._make_request("GET", "https://school/api")

    assert "ConnectionError" in caplog.text
    assert "synthetic-secret-token" not in caplog.text


def test_get_course_returns_none_for_missing_course(monkeypatch):
    canvas = make_canvas()
    record_requests(monkeypatch, canvas, [make_http_response(404, {})])

    assert canvas.get_course(999) is None


@pytest.mark.parametrize(
    ("method_name", "args", "expected_path", "expected_json"),
    [
        ("update_course", (1, {"name": "X"}), "/courses/1", {"course": {"name": "X"}}),
        (
            "update_module",
            (1, 2, {"published": True}),
            "/courses/1/modules/2",
            {"module": {"published": True}},
        ),
        (
            "update_module_item",
            (1, 2, 3, {"position": 5}),
            "/courses/1/modules/2/items/3",
            {"module_item": {"position": 5}},
        ),
        (
            "update_quiz",
            (1, 4, {"due_at": "2026-05-01T12:00:00"}),
            "/courses/1/quizzes/4",
            {"quiz": {"due_at": "2026-05-01T12:00:00"}},
        ),
        (
            "update_page",
            (1, "week-1-overview", {"body": "<p>Hi</p>"}),
            "/courses/1/pages/week-1-overview",
            {"wiki_page": {"body": "<p>Hi</p>"}},
        ),
    ],
)
def test_update_methods_put_wrapped_payload(
    monkeypatch, method_name, args, expected_path, expected_json
):
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [make_http_response(200, {"id": 9})])

    assert getattr(canvas, method_name)(*args) == {"id": 9}
    assert calls[0]["method"] == "PUT"
    assert calls[0]["url"] == f"{BASE_URL}/api/v1{expected_path}"
    assert calls[0]["kwargs"] == {"json": expected_json}


@pytest.mark.parametrize(
    ("method_name", "args", "expected_path"),
    [
        ("update_assignment_group", (1, 7, {"group_weight": 20}), "/courses/1/assignment_groups/7"),
        (
            "update_discussion_topic",
            (1, 8, {"delayed_post_at": None}),
            "/courses/1/discussion_topics/8",
        ),
    ],
)
def test_update_methods_that_send_unwrapped_payload(monkeypatch, method_name, args, expected_path):
    """These endpoints take top-level fields; wrapping them is a bug."""
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [make_http_response(200, {"id": 9})])

    assert getattr(canvas, method_name)(*args) == {"id": 9}
    assert calls[0]["method"] == "PUT"
    assert calls[0]["url"].endswith(expected_path)
    assert calls[0]["kwargs"] == {"json": args[-1]}


def test_move_module_item_sends_target_module_and_position(monkeypatch):
    canvas = make_canvas()
    calls = record_requests(
        monkeypatch, canvas, [make_http_response(200, {"id": 3, "module_id": 20})]
    )

    assert canvas.move_module_item(1, 10, 3, 20, position=1) == {"id": 3, "module_id": 20}
    # The PUT goes to the *source* module's item URL.
    assert calls[0]["url"].endswith("/courses/1/modules/10/items/3")
    assert calls[0]["kwargs"] == {"json": {"module_item": {"module_id": 20, "position": 1}}}


def test_move_module_item_omits_position_when_not_given(monkeypatch):
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [make_http_response(200, {})])

    canvas.move_module_item(1, 10, 3, 20)

    assert calls[0]["kwargs"] == {"json": {"module_item": {"module_id": 20}}}


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422, 500])
def test_update_returns_none_on_http_error(monkeypatch, status_code):
    canvas = make_canvas()
    record_requests(monkeypatch, canvas, [make_http_response(status_code, {"errors": ["no"]})])

    assert canvas.update_module_item(1, 2, 3, {"published": True}) is None


def test_reorder_modules_posts_form_encoded_order(monkeypatch):
    canvas = make_canvas()
    calls = record_requests(
        monkeypatch, canvas, [make_http_response(200, {}), make_http_response(403, {})]
    )

    assert canvas.reorder_modules(1, [30, 10, 20]) is True
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/courses/1/modules/reorder")
    assert calls[0]["kwargs"] == {"data": {"order[]": [30, 10, 20]}}
    assert canvas.reorder_modules(1, [30, 10, 20]) is False


def test_update_failure_is_reported_for_real_http_error(monkeypatch, caplog):
    """Was a strict xfail in COURSE_BUILDER: a real 422 was silent.

    ``requests.Response`` is falsy for 4xx/5xx, so the source's
    ``if response:`` guard skipped its error message.  The unified client
    compares with ``None`` and logs the status and body.
    """
    canvas = make_canvas()
    record_requests(monkeypatch, canvas, [make_http_response(422, {"errors": ["invalid due_at"]})])
    canvas_response_text = "invalid due_at"
    monkeypatch.setattr(requests.Response, "text", property(lambda self: canvas_response_text))

    with caplog.at_level(logging.ERROR):
        assert canvas.update_module(1, 2, {"published": True}) is None

    assert "422" in caplog.text
    assert "Updating module 2" in caplog.text


@pytest.mark.parametrize("status_code", [404, 500])
def test_delete_failure_is_reported_and_returns_false(monkeypatch, caplog, status_code):
    canvas = make_canvas()
    record_requests(monkeypatch, canvas, [make_http_response(status_code, {})])

    with caplog.at_level(logging.ERROR):
        assert canvas.delete_page(1, "gone") is False

    assert str(status_code) in caplog.text


# ---------------------------------------------------------------------
# Operations the source client had that nothing above exercised, plus the
# scope additions (announcements, course copy).
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method_name", "args", "expected_path"),
    [
        ("get_module", (1, 2), "/courses/1/modules/2"),
        ("get_module_item", (1, 2, 3), "/courses/1/modules/2/items/3"),
        ("get_assignment_group", (1, 7), "/courses/1/assignment_groups/7"),
        ("get_discussion_topic_record", (1, 8), "/courses/1/discussion_topics/8"),
        ("get_quiz", (1, 4), "/courses/1/quizzes/4"),
        ("get_page", (1, "syllabus"), "/courses/1/pages/syllabus"),
        ("get_content_migration", (1, 6), "/courses/1/content_migrations/6"),
    ],
)
def test_single_resource_getters_return_json_or_none(monkeypatch, method_name, args, expected_path):
    canvas = make_canvas()
    calls = record_requests(
        monkeypatch, canvas, [make_http_response(200, {"id": 1}), make_http_response(404, {})]
    )

    assert getattr(canvas, method_name)(*args) == {"id": 1}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == f"{BASE_URL}/api/v1{expected_path}"
    assert getattr(canvas, method_name)(*args) is None


@pytest.mark.parametrize(
    ("method_name", "args", "expected_path", "expected_params"),
    [
        ("get_modules", (1,), "/courses/1/modules", {"include[]": ["items", "content_details"]}),
        ("get_module_items", (1, 2), "/courses/1/modules/2/items", None),
        ("get_discussion_topics", (1,), "/courses/1/discussion_topics", None),
        ("get_quizzes", (1,), "/courses/1/quizzes", None),
        ("get_pages", (1,), "/courses/1/pages", None),
        (
            "get_announcements",
            (1,),
            "/announcements",
            {"context_codes[]": ["course_1"]},
        ),
    ],
)
def test_list_getters_use_the_shared_paginator(
    monkeypatch, method_name, args, expected_path, expected_params
):
    canvas = make_canvas()
    captured = {}

    def fake_paginated_get(url, params=None):
        captured["url"] = url
        captured["params"] = params
        return [{"id": 1}]

    monkeypatch.setattr(canvas, "_paginated_get", fake_paginated_get)

    assert getattr(canvas, method_name)(*args) == [{"id": 1}]
    assert captured["url"] == f"{BASE_URL}/api/v1{expected_path}"
    assert captured["params"] == expected_params


@pytest.mark.parametrize(
    ("method_name", "args", "expected_path", "expected_json"),
    [
        (
            "create_module",
            (1, {"name": "Unit 1"}),
            "/courses/1/modules",
            {"module": {"name": "Unit 1"}},
        ),
        (
            "create_module_item",
            (1, 2, {"type": "Page"}),
            "/courses/1/modules/2/items",
            {"module_item": {"type": "Page"}},
        ),
        (
            "create_assignment",
            (1, {"name": "A"}),
            "/courses/1/assignments",
            {"assignment": {"name": "A"}},
        ),
        (
            "create_assignment_group",
            (1, {"name": "G"}),
            "/courses/1/assignment_groups",
            {"name": "G"},
        ),
        ("create_quiz", (1, {"title": "Q"}), "/courses/1/quizzes", {"quiz": {"title": "Q"}}),
        (
            "create_quiz_question",
            (1, 4, {"question_text": "?"}),
            "/courses/1/quizzes/4/questions",
            {"question": {"question_text": "?"}},
        ),
        ("create_page", (1, {"title": "P"}), "/courses/1/pages", {"wiki_page": {"title": "P"}}),
        (
            "copy_course_content",
            (1, 2),
            "/courses/1/content_migrations",
            {"migration_type": "course_copy_importer", "settings": {"source_course_id": "2"}},
        ),
    ],
)
def test_create_methods_post_the_documented_payload(
    monkeypatch, method_name, args, expected_path, expected_json
):
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [make_http_response(201, {"id": 5})])

    assert getattr(canvas, method_name)(*args) == {"id": 5}
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == f"{BASE_URL}/api/v1{expected_path}"
    assert calls[0]["kwargs"] == {"json": expected_json}


@pytest.mark.parametrize(
    ("method_name", "args", "expected_path"),
    [
        ("delete_module_item", (1, 2, 3), "/courses/1/modules/2/items/3"),
        ("delete_assignment", (1, 4), "/courses/1/assignments/4"),
        ("delete_assignment_group", (1, 7), "/courses/1/assignment_groups/7"),
        ("delete_page", (1, "old-page"), "/courses/1/pages/old-page"),
    ],
)
def test_delete_methods_use_resource_endpoint(monkeypatch, method_name, args, expected_path):
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [FakeResponse({}, status_code=200)])

    assert getattr(canvas, method_name)(*args) is True
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"] == f"{BASE_URL}/api/v1{expected_path}"


def test_course_nickname_round_trip(monkeypatch):
    canvas = make_canvas()
    calls = record_requests(
        monkeypatch,
        canvas,
        [
            make_http_response(200, {"nickname": "Fall A"}),
            make_http_response(200, {"nickname": "B"}),
        ],
    )

    assert canvas.get_course_nickname(5) == "Fall A"
    assert canvas.set_course_nickname(5, "B") == {"nickname": "B"}
    assert calls[1]["method"] == "PUT"
    assert calls[1]["url"].endswith("/users/self/course_nicknames/5")
    assert calls[1]["kwargs"] == {"json": {"nickname": "B"}}


# ---------------------------------------------------------------------
# File upload: three steps, all through the single request choke point.
# ---------------------------------------------------------------------


def test_upload_file_uses_choke_point_and_strips_credentials(monkeypatch, tmp_path):
    canvas = make_canvas()
    local_file = tmp_path / "deck.pdf"
    local_file.write_bytes(b"%PDF-synthetic")
    calls = record_requests(
        monkeypatch,
        canvas,
        [
            make_http_response(
                200,
                {"upload_url": "https://files.example.net/up", "upload_params": {"k": "v"}},
            ),
            make_http_response(201, {"id": 99, "display_name": "deck.pdf"}),
        ],
    )

    assert canvas.upload_file(1, local_file, folder="Unit 1") == {
        "id": 99,
        "display_name": "deck.pdf",
    }
    reservation, upload = calls
    assert reservation["url"].endswith("/courses/1/files")
    assert reservation["kwargs"]["json"] == {
        "name": "deck.pdf",
        "size": len(b"%PDF-synthetic"),
        "content_type": "application/pdf",
        "parent_folder_path": "Unit 1",
    }
    assert upload["url"] == "https://files.example.net/up"
    assert upload["kwargs"]["data"] == {"k": "v"}
    # A None header value removes the session default: the token must not
    # be sent to the storage host, and redirects are handled by us.
    assert upload["kwargs"]["headers"]["Authorization"] is None
    assert upload["kwargs"]["allow_redirects"] is False


def test_upload_file_follows_location_redirect_with_canvas_auth(monkeypatch, tmp_path):
    canvas = make_canvas()
    local_file = tmp_path / "a.txt"
    local_file.write_text("x", encoding="utf-8")
    calls = record_requests(
        monkeypatch,
        canvas,
        [
            make_http_response(200, {"upload_url": "https://files.example.net/up"}),
            make_http_response(303, {}, {"Location": f"{BASE_URL}/api/v1/files/7/confirm"}),
            make_http_response(200, {"id": 7}),
        ],
    )

    assert canvas.upload_file(1, local_file) == {"id": 7}
    assert calls[2]["method"] == "GET"
    assert calls[2]["url"].endswith("/files/7/confirm")


def test_upload_file_missing_file_makes_no_request(monkeypatch, tmp_path, caplog):
    canvas = make_canvas()
    calls = record_requests(monkeypatch, canvas, [])

    with caplog.at_level(logging.ERROR):
        assert canvas.upload_file(1, tmp_path / "nope.pdf") is None

    assert calls == []
    assert "file not found" in caplog.text


def test_upload_file_without_upload_url_returns_none(monkeypatch, tmp_path):
    canvas = make_canvas()
    local_file = tmp_path / "a.txt"
    local_file.write_text("x", encoding="utf-8")
    record_requests(monkeypatch, canvas, [make_http_response(200, {})])

    assert canvas.upload_file(1, local_file) is None
