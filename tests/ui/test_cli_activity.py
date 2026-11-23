"""Tests for the command line's remote-activity display.

Every test draws into a recorded Rich console writing to an in-memory
buffer, so nothing reaches the real terminal and the rendered text
can be inspected.  All names, ids and tokens here are synthetic.
"""

from __future__ import annotations

import io
import threading

import pytest
from rich.console import Console

from coursewright.core import traffic
from coursewright.core.traffic import bus as traffic_bus
from coursewright.ui.cli.activity import (
    RemoteActivityDisplay,
    describe_operation,
    describe_service,
)


@pytest.fixture(autouse=True)
def clean_traffic_bus():
    traffic.reset_for_tests()
    yield
    traffic.reset_for_tests()


def make_recorded_console(*, is_terminal: bool) -> Console:
    return Console(
        file=io.StringIO(),
        record=True,
        force_terminal=is_terminal,
        width=120,
        color_system=None,
        _environ={"TERM": "xterm-256color"},
    )


def subscriber_count() -> int:
    return len(traffic_bus._traffic_subscribers)


def canvas_event(target: str, status: int, method: str = "GET") -> traffic.TrafficEvent:
    return traffic.TrafficEvent("canvas", method, target, status, 12)


# -- what the operation label may contain --------------------------------


def test_describe_service_names_known_and_unknown_sources():
    assert describe_service("canvas") == "Canvas"
    assert describe_service("llm") == "LLM"
    assert describe_service("panopto") == "Panopto"


def test_describe_operation_masks_identifiers():
    assert describe_operation("courses/101/users/2002") == "courses/:id/users/:id"
    assert describe_operation("chat.completions") == "chat.completions"
    assert (
        describe_operation("users/sis_login_id:student@example.edu/profile")
        == "users/:id/profile"
    )


def test_describe_operation_drops_query_string():
    shown = describe_operation("courses/101/users?access_token=SECRET-TOKEN")
    assert shown == "courses/:id/users"


def test_describe_operation_caps_length():
    shown = describe_operation("/".join(["assignments"] * 30))
    assert len(shown) == 60
    assert shown.endswith("...")


# -- on a terminal ---------------------------------------------------------


def test_spinner_names_service_operation_count_and_last_status():
    console = make_recorded_console(is_terminal=True)

    with RemoteActivityDisplay("Fetching submissions", console=console) as activity:
        traffic.publish(canvas_event("courses/101/assignments", 0))
        traffic.publish(canvas_event("courses/101/assignments", 200))
        traffic.publish(canvas_event("courses/101/assignments", 0))
        traffic.publish(canvas_event("courses/101/assignments", 200))
        assert activity.finished_request_count == 2
        assert activity.last_status_label == "200"

    rendered = console.export_text()
    assert "Fetching submissions" in rendered
    assert "Canvas" in rendered
    assert "GET courses/:id/assignments" in rendered
    assert "requesting…" in rendered
    assert "1 request " in rendered
    assert "2 requests" in rendered
    assert "last 200" in rendered


def test_spinner_shows_llm_calls_and_errors():
    console = make_recorded_console(is_terminal=True)

    with RemoteActivityDisplay("Drafting replies", console=console) as activity:
        traffic.publish(
            traffic.TrafficEvent("llm", "example-model-mini", "chat.completions", 0, 0)
        )
        traffic.publish(
            traffic.TrafficEvent("llm", "example-model-mini", "chat.completions", -1, 30)
        )
        assert activity.finished_request_count == 1
        assert activity.last_status_label == "error"

    rendered = console.export_text()
    assert "LLM" in rendered
    assert "example-model-mini chat.completions" in rendered
    assert "last error" in rendered


def test_failed_http_status_is_counted_and_shown():
    console = make_recorded_console(is_terminal=True)

    with RemoteActivityDisplay("Posting comment", console=console):
        traffic.publish(canvas_event("courses/101/comments", 500, method="POST"))

    assert "last 500" in console.export_text()


def test_progress_bar_shows_counts_when_total_is_known():
    console = make_recorded_console(is_terminal=True)

    with RemoteActivityDisplay("Grading", total=4, console=console) as activity:
        traffic.publish(canvas_event("courses/101/submissions", 200))
        activity.advance()
        activity.advance(2)
        assert activity.completed_steps == 3

    rendered = console.export_text()
    assert "Grading" in rendered
    assert "0/4" in rendered
    assert "3/4" in rendered
    assert "━" in rendered  # the bar itself


def test_no_progress_bar_without_a_total():
    console = make_recorded_console(is_terminal=True)

    with RemoteActivityDisplay("Fetching", console=console):
        traffic.publish(canvas_event("courses", 200))

    assert "━" not in console.export_text()


def test_total_can_be_set_after_the_first_page():
    console = make_recorded_console(is_terminal=True)

    with RemoteActivityDisplay("Fetching pages", console=console) as activity:
        traffic.publish(canvas_event("courses/101/users", 200))
        activity.set_total(5)
        activity.advance()

    rendered = console.export_text()
    assert "1/5" in rendered
    assert "━" in rendered


def test_display_unsubscribes_on_exit_even_after_an_error():
    console = make_recorded_console(is_terminal=True)

    with (
        pytest.raises(RuntimeError, match="library call failed"),
        RemoteActivityDisplay("Fetching", console=console),
    ):
        assert subscriber_count() == 1
        raise RuntimeError("library call failed")

    assert subscriber_count() == 0
    # A later event must not draw anything more.
    text_at_exit = console.export_text(clear=False)
    traffic.publish(canvas_event("courses/101/late", 200))
    assert console.export_text() == text_at_exit


def test_events_from_a_worker_thread_are_rendered():
    console = make_recorded_console(is_terminal=True)

    def do_remote_work() -> None:
        for _ in range(5):
            traffic.publish(canvas_event("courses/101/pages", 200))

    with RemoteActivityDisplay("Fetching pages", console=console) as activity:
        worker = threading.Thread(target=do_remote_work)
        worker.start()
        worker.join()
        assert activity.finished_request_count == 5

    assert "5 requests" in console.export_text()


# -- nothing sensitive reaches the display (card acceptance 5) -------------


def test_tokens_bodies_and_student_data_never_reach_the_display():
    console = make_recorded_console(is_terminal=True)
    secret_token = "SECRET-TOKEN-abc123"
    student_name = "Example Student"
    student_address = "student@example.edu"
    student_canvas_id = "2002"
    response_body = f'{{"name": "{student_name}", "login_id": "{student_address}"}}'
    request_url = (
        "https://canvas.example.edu/api/v1/courses/101/users/"
        f"{student_canvas_id}?access_token={secret_token}"
    )

    with RemoteActivityDisplay("Fetching roster", console=console):
        # A well-behaved client, through the publish helpers.
        traffic.publish_http_round_trip("canvas", "GET", request_url, 0, 0.0)
        traffic.publish_http_round_trip("canvas", "GET", request_url, 200, 0.0)
        traffic.publish_http_response_echo("canvas", "GET", request_url, response_body)
        traffic.publish_payload_echo(
            "llm", "example-model-mini", "chat.completions", "out",
            f"Grade the post by {student_name}",
        )
        # A careless publisher that put everything in the target.
        traffic.publish(
            canvas_event(
                f"users/sis_login_id:{student_address}/{student_name}"
                f"?access_token={secret_token}",
                200,
            )
        )

    rendered = console.export_text()
    assert "Canvas" in rendered
    assert "courses/:id/users/:id" in rendered
    assert "2 requests" in rendered  # chunk events are not counted
    for forbidden in (
        secret_token,
        "access_token",
        student_name,
        student_address,
        student_canvas_id,
        "login_id",
        "Grade the post",
    ):
        assert forbidden not in rendered


def test_event_text_cannot_inject_rich_markup():
    console = make_recorded_console(is_terminal=True)

    with RemoteActivityDisplay("Fetching [bold]all[/bold]", console=console):
        traffic.publish(
            traffic.TrafficEvent("canvas", "[red]GET[/red]", "courses", 200, 1)
        )

    rendered = console.export_text()
    assert "Fetching [bold]all[/bold]" in rendered
    assert "[red]GET[/red]" in rendered


# -- not a terminal --------------------------------------------------------


def test_quiet_when_console_is_not_a_terminal():
    console = make_recorded_console(is_terminal=False)

    with RemoteActivityDisplay("Fetching", total=3, console=console) as activity:
        assert subscriber_count() == 0
        traffic.publish(canvas_event("courses/101/assignments", 0))
        traffic.publish(canvas_event("courses/101/assignments", 200))
        activity.set_total(4)
        activity.advance()
        assert activity.completed_steps == 1

    assert console.export_text() == ""
    assert console.file.getvalue() == ""
