"""Every provider call publishes traffic events (AGENTS.md 3.16).

New with the port: discussorator's LLM client published to a bus that
lived in its TUI and had no test of its own.  Here the events come
from the client's single choke point, `LLMClient._make_request`, and
these tests subscribe to the real bus in ``core/traffic``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest

import coursewright.core.llm
from coursewright.core import traffic
from coursewright.core.llm import cost_tracker
from coursewright.core.llm.client import LLMClient, LLMConfig, LLMProvider

SYNTHETIC_API_KEY = "test_key_12345"  # pragma: allowlist secret
LLM_PACKAGE_ROOT = Path(coursewright.core.llm.__file__).parent


@pytest.fixture(autouse=True)
def no_usage_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these tests away from the real usage file."""
    monkeypatch.setattr(cost_tracker, "record_usage", lambda **fields: None)


@pytest.fixture
def published_events() -> Iterator[list[traffic.TrafficEvent]]:
    """Subscribe to the real traffic bus for the length of one test."""
    events: list[traffic.TrafficEvent] = []
    traffic.reset_for_tests()
    traffic.subscribe(events.append)
    yield events
    traffic.reset_for_tests()


def round_trips(events: list[traffic.TrafficEvent]) -> list[traffic.TrafficEvent]:
    """The in-flight and closing events, without payload echo chunks."""
    payload_statuses = {traffic.STATUS_OUTBOUND_CHUNK, traffic.STATUS_INBOUND_CHUNK}
    return [event for event in events if event.status not in payload_statuses]


def client_answering(mock_httpx: Mock, provider: LLMProvider, model: str, reply: Mock) -> LLMClient:
    mock_client_instance = Mock()
    mock_client_instance.post.return_value = reply
    mock_httpx.return_value = mock_client_instance
    return LLMClient(LLMConfig(provider=provider, model=model, api_key=SYNTHETIC_API_KEY))


def openai_reply() -> Mock:
    reply = Mock()
    reply.status_code = 200
    reply.json.return_value = {
        "choices": [{"message": {"content": "Reply text."}, "finish_reason": "stop"}],
        "model": "gpt-4o-mini",
        "usage": {"total_tokens": 45},
    }
    return reply


def anthropic_reply() -> Mock:
    reply = Mock()
    reply.status_code = 200
    reply.json.return_value = {
        "content": [{"text": "Reply text."}],
        "model": "claude-sonnet-4-6",
        "usage": {"input_tokens": 30, "output_tokens": 12},
    }
    return reply


@pytest.mark.parametrize(
    ("provider", "model", "build_reply", "expected_target"),
    [
        (LLMProvider.OPENAI, "gpt-4o-mini", openai_reply, "chat.completions"),
        (LLMProvider.ANTHROPIC, "claude-sonnet-4-6", anthropic_reply, "messages"),
    ],
)
@patch("httpx.Client")
def test_every_provider_call_publishes_in_flight_then_status(
    mock_httpx, published_events, provider, model, build_reply, expected_target
):
    client = client_answering(mock_httpx, provider, model, build_reply())

    client.generate_sync("Outbound prompt text.")

    in_flight, completed = round_trips(published_events)
    assert (in_flight.source, in_flight.method, in_flight.target) == (
        "llm",
        model,
        expected_target,
    )
    assert in_flight.status == traffic.STATUS_IN_FLIGHT
    assert completed.status == 200
    assert completed.duration_ms >= 0
    assert all(not event.payload for event in published_events)
    assert len(published_events) == 2


@patch("httpx.Client")
async def test_async_generate_publishes_too(mock_httpx, published_events):
    client = client_answering(mock_httpx, LLMProvider.OPENAI, "gpt-4o-mini", openai_reply())

    await client.generate("Outbound prompt text.")

    assert [event.status for event in round_trips(published_events)] == [0, 200]


@pytest.mark.parametrize("provider", [LLMProvider.OPENAI, LLMProvider.ANTHROPIC])
@patch("httpx.Client")
def test_http_error_publishes_the_failing_status(mock_httpx, published_events, provider):
    request = httpx.Request("POST", "https://provider.example/v1")
    failed_reply = Mock()
    failed_reply.raise_for_status.side_effect = httpx.HTTPStatusError(
        "failed", request=request, response=httpx.Response(429, json={}, request=request)
    )
    client = client_answering(mock_httpx, provider, "synthetic-model", failed_reply)

    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        client.generate_sync("Outbound prompt text.")

    assert [event.status for event in round_trips(published_events)] == [0, 429]


@patch("httpx.Client")
def test_connection_failure_publishes_an_errored_event(mock_httpx, published_events):
    mock_client_instance = Mock()
    mock_client_instance.post.side_effect = httpx.ConnectError("connection refused")
    mock_httpx.return_value = mock_client_instance
    client = LLMClient(LLMConfig(model="gpt-4o-mini", api_key=SYNTHETIC_API_KEY))

    with pytest.raises(httpx.ConnectError):
        client.generate_sync("Outbound prompt text.")

    assert [event.status for event in round_trips(published_events)] == [
        traffic.STATUS_IN_FLIGHT,
        traffic.STATUS_ERRORED,
    ]


@patch("httpx.Client")
def test_a_broken_subscriber_never_breaks_the_call(mock_httpx, published_events):
    def broken_subscriber(event: traffic.TrafficEvent) -> None:
        raise RuntimeError("subscriber went away")

    traffic.subscribe(broken_subscriber)
    client = client_answering(mock_httpx, LLMProvider.OPENAI, "gpt-4o-mini", openai_reply())

    assert client.generate_sync("Outbound prompt text.").content == "Reply text."


@patch("httpx.Client")
def test_the_api_key_never_appears_in_traffic(mock_httpx, published_events):
    client = client_answering(mock_httpx, LLMProvider.OPENAI, "gpt-4o-mini", openai_reply())

    client.generate_sync("Outbound prompt text.")

    for event in published_events:
        assert SYNTHETIC_API_KEY not in f"{event.method} {event.target} {event.payload}"


def test_only_the_choke_point_talks_to_the_provider():
    """One choke point: `self.client.post` appears once, in `_make_request`."""
    tree = ast.parse((LLM_PACKAGE_ROOT / "client.py").read_text(encoding="utf-8"))
    functions_that_post = [
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr in {"post", "request", "stream", "send"}
    ]
    assert functions_that_post == ["_make_request"]


def test_llm_package_imports_no_user_interface_library():
    """Acceptance: no rich, typer, textual or coursewright.ui under core/llm."""
    forbidden_roots = {"rich", "typer", "textual"}
    violations = []
    for source_path in LLM_PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names = [node.module]
            else:
                continue
            for imported_name in imported_names:
                if imported_name.split(".")[0] in forbidden_roots or imported_name.startswith(
                    "coursewright.ui"
                ):
                    violations.append(f"{source_path.name}: {imported_name}")
    assert not violations, violations
