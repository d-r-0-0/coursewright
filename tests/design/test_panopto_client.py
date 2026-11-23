"""House-style client contract beyond the source URL-helper regressions."""

from unittest.mock import Mock

import pytest
import requests

from coursewright.design.media import panopto_api as api
from coursewright.design.media.panopto_auth import (
    PanoptoClientCredentialsAuth,
    PanoptoPasswordGrantAuth,
    PanoptoTokenAuth,
)

BASE_URL = "https://panopto.example.edu/Panopto"


@pytest.fixture
def client(panopto_transport):
    return api.PanoptoClient(BASE_URL, auth_strategy=PanoptoTokenAuth("private-token"))


def test_oauth_fallback_cache_and_typed_session(panopto_transport, http_response):
    panopto_transport.side_effect = [
        http_response(401),
        http_response(payload={"access_token": "private-token"}),
        http_response(payload={"Id": "session-one", "Urls": {"ViewerUrl": "viewer"}}),
        http_response(payload={"Id": "session-two", "Urls": None}),
    ]
    client = api.PanoptoClient(BASE_URL, api.PanoptoClientCredentials("id", "value"))
    session = client.get_session("session-one")
    assert isinstance(session, api.PanoptoSession)
    assert session.id == session["Id"] == "session-one"
    assert session.urls.viewer_url == session["Urls"]["ViewerUrl"] == "viewer"
    assert client.get_session("session-two").urls.viewer_url is None
    calls = panopto_transport.call_args_list
    assert [call.args[0] for call in calls] == ["POST", "POST", "GET", "GET"]
    assert calls[0].kwargs["auth"] == ("id", "value")
    assert calls[1].kwargs["data"]["client_secret"] == "value"  # pragma: allowlist secret
    assert calls[2].kwargs["headers"]["Authorization"] == "Bearer private-token"
    assert "private-token" not in repr(client)


@pytest.mark.parametrize("timeout,expected", [(90, 30), (5, 5), (0, 30), (-1, 30)])
def test_timeout_ceiling(panopto_transport, http_response, timeout, expected):
    panopto_transport.side_effect = [http_response(payload={})]
    client = api.PanoptoClient(BASE_URL, "private-token", timeout)
    client.get_session("one")
    assert panopto_transport.call_args.kwargs["timeout"] == expected


def test_search_continuation_and_limit(client, panopto_transport, http_response):
    panopto_transport.side_effect = [
        http_response(
            payload={"Results": [{"Id": "one"}], "Continuation": {"NextContinuationToken": "next"}}
        ),
        http_response(payload={"Results": [{"Id": "two"}, {"Id": "three"}]}),
    ]
    sessions = client.search_sessions("Lecture", max_results=2)
    assert [session.id for session in sessions] == ["one", "two"]
    assert panopto_transport.call_args_list[0].kwargs["params"] == {"searchQuery": "Lecture"}
    assert panopto_transport.call_args.kwargs["params"] == {
        "searchQuery": "Lecture",
        "continuationToken": "next",
    }


def test_folder_zero_based_pages_stop_on_repeat(client, panopto_transport, http_response):
    panopto_transport.side_effect = [
        http_response(payload={"Results": [{"Id": "one"}]}),
        http_response(payload={"Results": [{"Id": "one"}, {"Id": "two"}]}),
        http_response(payload={"Results": [{"Id": "two"}]}),
    ]
    client.folder_id = "configured-folder"
    assert [session.id for session in client.list_folder_sessions()] == ["one", "two"]
    calls = panopto_transport.call_args_list
    assert [call.kwargs["params"]["pageNumber"] for call in calls] == ["0", "1", "2"]
    assert all(call.args[1].endswith("/folders/configured-folder/sessions") for call in calls)


@pytest.mark.parametrize("operation", ["search_sessions", "list_folder_sessions"])
def test_empty_page_stops(client, panopto_transport, http_response, operation):
    panopto_transport.side_effect = [http_response(payload={"Results": []})]
    assert getattr(client, operation)("example") == []
    assert panopto_transport.call_count == 1


def test_folder_requires_configuration(client, panopto_transport):
    with pytest.raises(ValueError, match="folder_id"):
        client.list_folder_sessions()
    panopto_transport.assert_not_called()


def test_retry_after_and_backoff(client, panopto_transport, http_response, monkeypatch):
    sleep = Mock()
    monkeypatch.setattr(api.time, "sleep", sleep)
    panopto_transport.side_effect = [
        http_response(429, headers={"Retry-After": "3"}),
        http_response(503),
        http_response(payload={"Results": []}),
    ]
    assert client.list_folder_sessions("example") == []
    assert [call.args[0] for call in sleep.call_args_list] == [3, 2]


@pytest.mark.parametrize("status,count", [(404, 1), (503, 5)])
def test_retry_exhaustion_and_client_errors(
    client, panopto_transport, http_response, monkeypatch, status, count
):
    monkeypatch.setattr(api.time, "sleep", Mock())
    panopto_transport.side_effect = [http_response(status) for _ in range(count)]
    with pytest.raises(requests.HTTPError):
        client.list_folder_sessions("example")
    assert panopto_transport.call_count == count


def test_unsigned_caption_then_authenticated_fallback(
    client, panopto_transport, http_response, tmp_path
):
    panopto_transport.side_effect = [http_response(403), http_response(content=b"WEBVTT")]
    destination = tmp_path / "captions" / "example.vtt"
    client.download_caption_file(BASE_URL + "/captions/example", destination)
    assert destination.read_bytes() == b"WEBVTT"
    assert "headers" not in panopto_transport.call_args_list[0].kwargs
    assert panopto_transport.call_args.kwargs["headers"]["Authorization"] == "Bearer private-token"


def test_caption_failure_preserves_chain(client, panopto_transport, http_response, tmp_path):
    panopto_transport.side_effect = [http_response(404), http_response(404)]
    with pytest.raises(RuntimeError) as caught:
        client.download_caption_file(BASE_URL + "/captions/example", tmp_path / "out")
    assert isinstance(caught.value.__cause__, requests.HTTPError)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("operation", ["session", "search", "folder", "caption", "token"])
def test_every_public_round_trip_publishes_events(
    client, panopto_transport, http_response, traffic_events, tmp_path, operation
):
    if operation == "token":
        client = api.PanoptoClient(BASE_URL, api.PanoptoClientCredentials("id", "value"))
        panopto_transport.side_effect = [http_response(payload={"access_token": "private-token"})]
        assert client.authenticate()
    else:
        panopto_transport.side_effect = [
            http_response(payload={"Results": [], "Name": "private-body"})
        ]
        operations = {
            "session": lambda: client.get_session("one"),
            "search": lambda: client.search_sessions("private-query"),
            "folder": lambda: client.list_folder_sessions("example"),
            "caption": lambda: client.download_caption_file(
                BASE_URL + "/captions/example?key=private-query", tmp_path / "out"
            ),
        }
        operations[operation]()
    assert [event.status for event in traffic_events] == [0, 200]
    assert all(event.source == "panopto" and event.duration_ms >= 0 for event in traffic_events)
    for value in ("private-token", "private-body", "private-query"):
        assert value not in repr(traffic_events)


def test_retries_each_publish_events(
    client, panopto_transport, http_response, traffic_events, monkeypatch
):
    monkeypatch.setattr(api.time, "sleep", Mock())
    panopto_transport.side_effect = [http_response(500), http_response(payload={})]
    client.list_folder_sessions("example")
    assert [event.status for event in traffic_events] == [0, 500, 0, 200]


def test_transport_error_event_and_safe_log(client, panopto_transport, traffic_events, caplog):
    panopto_transport.side_effect = requests.Timeout("private-token")
    with pytest.raises(requests.Timeout):
        client.get_session("one")
    assert [event.status for event in traffic_events] == [0, -1]
    assert "private-token" not in caplog.text


def test_factory_resolves_selected_identity(monkeypatch, panopto_transport):
    loader = Mock(return_value=api.PanoptoClientCredentials("id", "value"))
    monkeypatch.setattr(api, "load_panopto_client_credentials", loader)
    client = api.create_panopto_client(BASE_URL, client_type="user", folder_id="example")
    loader.assert_called_once_with("user")
    assert client.folder_id == "example"
    assert isinstance(client.auth_strategy, PanoptoClientCredentialsAuth)
    loader.return_value = None
    monkeypatch.setenv("COURSEWRIGHT_API_TOKEN", "canvas-only-value")
    with pytest.raises(RuntimeError, match="No Panopto"):
        api.create_panopto_client(BASE_URL)
    panopto_transport.assert_not_called()


def test_strategy_reprs_hide_all_credentials():
    pair = api.PanoptoClientCredentials("private-id", "private-value")
    strategies = [
        PanoptoTokenAuth("private-token"),
        PanoptoClientCredentialsAuth(pair),
        PanoptoPasswordGrantAuth(pair, "private-user", "private-password"),
    ]
    for strategy in strategies:
        assert "private" not in repr(strategy)
