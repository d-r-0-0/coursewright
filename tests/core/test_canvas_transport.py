"""House-style transport guarantees across reads, writes, retries and failures."""

import ast
import inspect
from unittest.mock import Mock, patch

import pytest
import requests

from coursewright.core.canvas.client import CanvasAuthError, CanvasClient, CanvasRequestError
from coursewright.core.traffic import subscribe, unsubscribe


@pytest.fixture
def transport_client():
    client = CanvasClient("https://canvas.example.edu", "synthetic-token")
    client._session = Mock()
    client._rate_limit_delay = 0
    response = requests.Response()
    response.status_code = 200
    response._content = b"[]"
    response._content_consumed = True
    client._session.request.return_value = response
    return client


@pytest.fixture
def traffic_events():
    events = []
    subscriber = events.append
    subscribe(subscriber)
    try:
        yield events
    finally:
        unsubscribe(subscriber)


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"])
def test_every_verb_has_timeout_and_traffic(transport_client, traffic_events, method):
    transport_client._make_request(method, "https://canvas.example.edu/api/v1/courses")
    assert transport_client._session.request.call_args.kwargs["timeout"] == 30
    completed = [event for event in traffic_events if event.status >= 0]
    assert [event.status for event in completed] == [0, 200]
    assert all(event.source == "canvas" and event.method == method for event in completed)
    assert all(event.duration_ms >= 0 for event in completed)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, 30), (90, 30), (5, 5), ((None, 60), (30, 30)), ((2, 4), (2, 4))],
)
def test_timeout_cannot_be_disabled_or_exceed_ceiling(transport_client, requested, expected):
    transport_client._make_request("GET", transport_client.base_url, timeout=requested)
    assert transport_client._session.request.call_args.kwargs["timeout"] == expected


def test_retry_has_separate_traffic_and_timeout(transport_client, traffic_events):
    rate_limited = requests.Response()
    rate_limited.status_code = 429
    rate_limited.headers["Retry-After"] = "0"
    transport_client._session.request.side_effect = [
        rate_limited,
        transport_client._session.request.return_value,
    ]
    with patch("coursewright.core.canvas.client.time.sleep"):
        transport_client.get_courses()
    assert [event.status for event in traffic_events if event.status >= 0] == [
        0,
        429,
        0,
        200,
    ]
    assert all(
        call.kwargs["timeout"] == 30 for call in transport_client._session.request.call_args_list
    )


def test_network_failure_publishes_error(transport_client, traffic_events):
    transport_client._session.request.side_effect = requests.Timeout("synthetic timeout")
    with pytest.raises(CanvasRequestError):
        transport_client.get_course(42)
    assert [event.status for event in traffic_events] == [0, -1]


def test_pagination_and_download_share_transport(transport_client, traffic_events, tmp_path):
    transport_client.get_courses()
    downloaded = transport_client.download_submission_file(
        "https://canvas.example.edu/file", tmp_path / "submission.txt"
    )
    assert downloaded.read_bytes() == b"[]"
    assert transport_client._session.request.call_count == 2
    assert [event.status for event in traffic_events if event.status > 0] == [200, 200]


def test_only_make_request_opens_http_connections():
    """Guard against future operations bypassing timeout and traffic handling."""
    client_tree = ast.parse(inspect.getsource(CanvasClient))
    network_calls = []
    for method in client_tree.body[0].body:
        if not isinstance(method, ast.FunctionDef):
            continue
        for node in ast.walk(method):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if ast.unparse(node.func.value) == "self._session":
                network_calls.append((method.name, node.func.attr))
    assert network_calls == [("_make_request", "request")]


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_http_failures_never_return_successful_collections(transport_client, status):
    response = transport_client._session.request.return_value
    response.status_code = status
    response._content = b'{"errors":[{"message":"Unusual wording"}]}'
    response.headers["Retry-After"] = "0"
    expected = CanvasAuthError if status == 401 else CanvasRequestError
    with pytest.raises(expected):
        transport_client.get_courses()


@pytest.mark.parametrize("broken_page", [None, "outage", {}, [{"id": 2}, "bad"]])
def test_second_page_failure_discards_partial_results(transport_client, broken_page):
    first = requests.Response()
    first.status_code = 200
    first._content = b'[{"id":1}]'
    first.headers["Link"] = '<https://canvas.example.edu/page2>; rel="next"'
    if broken_page is None:
        second = None
    else:
        second = Mock(status_code=200)
        if broken_page == "outage":
            second.json.side_effect = ValueError("invalid JSON")
        else:
            second.json.return_value = broken_page
    transport_client._make_request = Mock(side_effect=[first, second])
    with pytest.raises(CanvasRequestError):
        transport_client.get_courses()


def test_auth_error_is_not_swallowed_by_legacy_grade_writer(transport_client):
    transport_client._session.request.return_value.status_code = 401
    with pytest.raises(CanvasAuthError):
        transport_client.submit_grade(1, 2, 3, 10)


def test_response_and_request_errors_do_not_log_private_bodies(transport_client, caplog):
    response = transport_client._session.request.return_value
    response.status_code = 500
    response._content = b"synthetic-private-student-body"
    with pytest.raises(CanvasRequestError):
        transport_client.get_courses()
    assert "synthetic-private-student-body" not in caplog.text
