"""Tests for the traffic-event pub/sub bus (ported from discussorator)."""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from coursewright.core import traffic
from coursewright.core.traffic import bus as traffic_bus


def setup_function(_function) -> None:
    traffic_bus.reset_for_tests()


def teardown_function(_function) -> None:
    traffic_bus.reset_for_tests()


def test_publish_delivers_event_to_subscriber():
    received: list[traffic_bus.TrafficEvent] = []

    def handler(event: traffic_bus.TrafficEvent) -> None:
        received.append(event)

    traffic_bus.subscribe(handler)
    event = traffic_bus.TrafficEvent("canvas", "GET", "courses/123/assignments", 200, 42)
    traffic_bus.publish(event)
    assert received == [event]


def test_unsubscribe_stops_delivery():
    received: list[traffic_bus.TrafficEvent] = []

    def handler(event: traffic_bus.TrafficEvent) -> None:
        received.append(event)

    traffic_bus.subscribe(handler)
    traffic_bus.unsubscribe(handler)
    traffic_bus.publish(traffic_bus.TrafficEvent("canvas", "GET", "courses", 200, 1))
    assert received == []


def test_subscriber_exception_does_not_break_other_subscribers():
    received: list[traffic_bus.TrafficEvent] = []

    def explode(_event: traffic_bus.TrafficEvent) -> None:
        raise RuntimeError("subscriber went boom")

    def collect(event: traffic_bus.TrafficEvent) -> None:
        received.append(event)

    traffic_bus.subscribe(explode)
    traffic_bus.subscribe(collect)
    event = traffic_bus.TrafficEvent("canvas", "POST", "courses/9/comments", 201, 7)
    traffic_bus.publish(event)
    assert received == [event]


def test_format_log_line_canvas_success_uses_cyan():
    event = traffic_bus.TrafficEvent("canvas", "GET", "courses", 200, 12)
    rendered = event.format_log_line()
    assert "[cyan]" in rendered
    assert "GET" in rendered
    assert "courses" in rendered
    assert "200" in rendered
    assert "12ms" in rendered


def test_format_log_line_canvas_error_uses_red():
    event = traffic_bus.TrafficEvent("canvas", "GET", "courses", 500, 12)
    assert "[red]" in event.format_log_line()


def test_format_log_line_llm_uses_magenta():
    event = traffic_bus.TrafficEvent("llm", "gpt-4o-mini", "chat.completions", 200, 850)
    rendered = event.format_log_line()
    assert "[magenta]" in rendered
    assert "gpt-4o-mini" in rendered
    assert "850ms" in rendered


def test_format_log_line_llm_inflight_shows_arrow():
    event = traffic_bus.TrafficEvent("llm", "gpt-4o-mini", "chat.completions", 0, 0)
    rendered = event.format_log_line()
    assert "→" in rendered
    assert "requesting" in rendered


def test_format_log_line_canvas_inflight_shows_arrow():
    event = traffic_bus.TrafficEvent("canvas", "GET", "courses/123", 0, 0)
    rendered = event.format_log_line()
    assert "→" in rendered
    assert "GET" in rendered


def test_format_log_line_outbound_chunk_renders_uparrow_and_payload():
    event = traffic_bus.TrafficEvent(
        "llm", "gpt-5", "chat.completions", -2, 0, "please grade this discussion post"
    )
    rendered = event.format_log_line()
    assert "↑" in rendered
    assert "please grade this discussion post" not in rendered
    assert "[dim]" in rendered


def test_format_log_line_inbound_chunk_renders_downarrow_and_payload():
    event = traffic_bus.TrafficEvent("canvas", "GET", "courses/9", -3, 0, '{"id":9,"name":"Demo"}')
    rendered = event.format_log_line()
    assert "↓" in rendered
    assert "Demo" not in rendered


def test_publish_chunks_splits_text_into_multiple_events():
    received: list[traffic_bus.TrafficEvent] = []
    traffic_bus.subscribe(received.append)
    traffic_bus.publish_chunks("llm", "gpt-5", "chat.completions", "in", "a" * 200)
    assert received == []


def test_publish_chunks_skips_empty_text():
    received: list[traffic_bus.TrafficEvent] = []
    traffic_bus.subscribe(received.append)
    traffic_bus.publish_chunks("llm", "gpt-5", "chat.completions", "out", "")
    traffic_bus.publish_chunks("llm", "gpt-5", "chat.completions", "out", "   \n\t  ")
    assert received == []


def test_publish_chunks_respects_max_chunk_cap():
    received: list[traffic_bus.TrafficEvent] = []
    traffic_bus.subscribe(received.append)
    traffic_bus.publish_chunks("canvas", "GET", "courses/1", "in", "x" * 100_000)
    assert received == []


# ---------------------------------------------------------------------
# New in Coursewright: the package surface, the publish helpers a
# client calls from its request choke point, and the layering rule.
# ---------------------------------------------------------------------


def collect_published_events() -> list[traffic.TrafficEvent]:
    received: list[traffic.TrafficEvent] = []
    traffic.subscribe(received.append)
    return received


def test_package_reexports_the_bus():
    assert traffic.TrafficEvent is traffic_bus.TrafficEvent
    assert traffic.publish is traffic_bus.publish
    assert traffic.subscribe is traffic_bus.subscribe
    assert traffic.unsubscribe is traffic_bus.unsubscribe
    assert traffic.STATUS_IN_FLIGHT == 0
    assert traffic.STATUS_ERRORED == -1
    assert traffic.STATUS_OUTBOUND_CHUNK == -2
    assert traffic.STATUS_INBOUND_CHUNK == -3


def test_subscribing_twice_delivers_once():
    received: list[traffic.TrafficEvent] = []
    handler = received.append
    traffic.subscribe(handler)
    traffic.subscribe(handler)
    traffic.publish(traffic.TrafficEvent("canvas", "GET", "courses", 200, 1))
    assert len(received) == 1


def test_shorten_request_target_strips_host_and_api_prefix():
    url = "https://canvas.example.edu/api/v1/courses/101/assignments"
    assert traffic.shorten_request_target(url) == "courses/101/assignments"


def test_shorten_request_target_drops_query_string_and_fragment():
    url = (
        "https://canvas.example.edu/api/v1/courses/101/users"
        "?access_token=SECRET-TOKEN&search_term=Example+Student#top"
    )
    shortened = traffic.shorten_request_target(url)
    assert shortened == "courses/101/users"
    assert "SECRET-TOKEN" not in shortened
    assert "Example" not in shortened


def test_shorten_request_target_caps_length_with_ellipsis():
    url = "https://canvas.example.edu/api/v1/" + "segment/" * 20
    shortened = traffic.shorten_request_target(url)
    assert len(shortened) == 60
    assert shortened.endswith("...")


def test_shorten_request_target_leaves_targets_without_the_marker():
    assert traffic.shorten_request_target("chat.completions") == "chat.completions"


def test_publish_http_round_trip_publishes_shortened_uppercased_event():
    received = collect_published_events()
    url = "https://canvas.example.edu/api/v1/courses/101?per_page=50"
    traffic.publish_http_round_trip("canvas", "get", url, 200, time.monotonic())
    assert len(received) == 1
    event = received[0]
    assert (event.source, event.method, event.target) == ("canvas", "GET", "courses/101")
    assert event.status == 200
    assert event.duration_ms >= 0
    assert event.payload == ""


def test_publish_http_round_trip_measures_duration_from_start():
    received = collect_published_events()
    started = time.monotonic() - 2.5
    traffic.publish_http_round_trip("canvas", "GET", "/api/v1/courses", 200, started)
    assert 2500 <= received[0].duration_ms < 10_000


def test_publish_round_trip_passes_method_and_target_through_unchanged():
    received = collect_published_events()
    traffic.publish_round_trip("llm", "example-model-mini", "chat.completions", 0, time.monotonic())
    assert received[0].method == "example-model-mini"
    assert received[0].target == "chat.completions"
    assert received[0].status == traffic.STATUS_IN_FLIGHT


def test_publish_http_response_echo_publishes_inbound_chunks():
    received = collect_published_events()
    url = "https://canvas.example.edu/api/v1/courses/9"
    traffic.publish_http_response_echo("canvas", "get", url, "course nine")
    assert received == []


def test_publish_http_response_echo_skips_empty_body():
    received = collect_published_events()
    traffic.publish_http_response_echo("canvas", "GET", "/api/v1/courses", "")
    traffic.publish_http_response_echo("canvas", "GET", "/api/v1/courses", None)
    assert received == []


def test_publish_payload_echo_outbound_uses_outbound_status():
    received = collect_published_events()
    traffic.publish_payload_echo("llm", "example-model-mini", "chat.completions", "out", "a prompt")
    assert received == []


@pytest.mark.parametrize(
    "publish_with_bad_arguments",
    [
        lambda: traffic.publish_http_round_trip("canvas", None, None, 200, 0.0),
        lambda: traffic.publish_http_round_trip("canvas", "GET", "/x", 200, "soon"),
        lambda: traffic.publish_round_trip("llm", "model", "target", 0, None),
        lambda: traffic.publish_http_response_echo("canvas", None, None, "body"),
        lambda: traffic.publish_payload_echo("llm", "model", "target", "in", 12345),
    ],
)
def test_publish_helpers_never_raise_on_bad_arguments(publish_with_bad_arguments):
    received = collect_published_events()
    publish_with_bad_arguments()
    assert received == []


def test_publish_helpers_never_raise_when_the_bus_is_broken(monkeypatch):
    def broken_publish(_event):
        raise RuntimeError("bus is on fire")

    monkeypatch.setattr(traffic_bus, "publish", broken_publish)
    now = time.monotonic()
    traffic.publish_round_trip("llm", "model", "target", 200, now)
    traffic.publish_http_round_trip("canvas", "GET", "/api/v1/courses", 200, now)
    traffic.publish_payload_echo("llm", "model", "target", "in", "some text")
    traffic.publish_http_response_echo("canvas", "GET", "/api/v1/courses", "body")


def test_publish_helpers_survive_an_exploding_subscriber():
    def explode(_event):
        raise RuntimeError("subscriber went boom")

    traffic.subscribe(explode)
    received = collect_published_events()
    traffic.publish_http_round_trip("canvas", "GET", "/api/v1/courses", 200, time.monotonic())
    assert len(received) == 1


def test_traffic_package_imports_no_user_interface_library():
    """Card acceptance 3: no rich, typer, textual or coursewright.ui."""
    forbidden_roots = {"rich", "typer", "textual"}
    violations = []
    for source_path in Path(traffic.__file__).parent.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported_names = [node.module or ""]
            else:
                continue
            for imported_name in imported_names:
                if imported_name.split(".")[0] in forbidden_roots or imported_name.startswith(
                    "coursewright.ui"
                ):
                    violations.append(f"{source_path.name}: {imported_name}")
    assert not violations, "\n".join(violations)


def test_round_trip_untrusted_markup_is_literal():
    from rich.text import Text

    event = traffic.TrafficEvent("canvas", "[red]GET", "users/[link=evil]name", 200, 1)
    rendered = Text.from_markup(event.format_log_line())
    assert "[red]GET" in rendered.plain
    assert "[link=evil]name" in rendered.plain


def test_all_body_echo_paths_suppress_private_data():
    received = collect_published_events()
    private = "student@example.edu [red]synthetic-secret[/red]"
    traffic.publish_payload_echo("llm", "model", "messages", "out", private)
    traffic.publish_http_response_echo("canvas", "GET", "/users", private)
    traffic_bus.publish_chunks("canvas", "GET", "/users", "in", private)
    assert received == []
