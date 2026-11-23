"""Tests verifying RemoteActivityDisplay appears for every remote CLI command group.

Each test uses a fake slow client that sleeps briefly and publishes traffic
events, and swaps the module's Rich console for a recorded terminal console
so the live spinner output can be inspected.
"""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from rich.console import Console
from typer.testing import CliRunner

from coursewright.core import traffic
from coursewright.core.canvas.models import (
    CanvasDiscussion,
    CanvasDiscussionEntry,
    CanvasUser,
)
from coursewright.ui.cli import assistant, auth, courses, discussions, ta
from coursewright.ui.cli.main import app


@pytest.fixture(autouse=True)
def clean_traffic_bus():
    traffic.reset_for_tests()
    yield
    traffic.reset_for_tests()


def make_terminal_console() -> Console:
    return Console(
        file=io.StringIO(),
        record=True,
        force_terminal=True,
        width=120,
        color_system=None,
        _environ={"TERM": "xterm-256color"},
    )


class FakeSlowCanvasClient:
    """Minimal slow Canvas client that publishes traffic events."""

    base_url = "https://canvas.example.edu"

    def _slow_call(self, target: str, method: str = "GET") -> None:
        traffic.publish_http_round_trip("canvas", method, target, 0, 0.0)
        time.sleep(0.01)
        traffic.publish_http_round_trip("canvas", method, target, 200, 10.0)

    def get_courses(self, enrollment_type: str = "teacher") -> list[dict]:
        self._slow_call("courses")
        return [
            {
                "id": 101,
                "name": "Example Course",
                "course_code": "CS-101",
                "term": {"name": "2026 Fall"},
                "total_students": 10,
                "workflow_state": "available",
            }
        ]

    def get_course(self, course_id: int) -> dict:
        self._slow_call(f"courses/{course_id}")
        return {"id": course_id, "name": "Example Course", "course_code": "CS-101"}

    def get_discussions(
        self, course_id: int, include_announcements: bool = False
    ) -> list[CanvasDiscussion]:
        self._slow_call(f"courses/{course_id}/discussion_topics")
        return [
            CanvasDiscussion(
                id=501,
                title="Week 1 Discussion",
                message="Introduce yourself.",
                course_id=course_id,
                replies_count=2,
            )
        ]

    def get_discussion_topic(
        self, course_id: int, discussion_id: int
    ) -> CanvasDiscussion:
        self._slow_call(f"courses/{course_id}/discussion_topics/{discussion_id}")
        return CanvasDiscussion(
            id=discussion_id,
            title="Week 1 Discussion",
            message="Introduce yourself.",
            course_id=course_id,
            replies_count=1,
        )

    def get_discussion_entries(
        self, course_id: int, discussion_id: int
    ) -> list[CanvasDiscussionEntry]:
        self._slow_call(f"courses/{course_id}/discussion_topics/{discussion_id}/view")
        return [
            CanvasDiscussionEntry(
                id=1,
                user_id=2001,
                discussion_id=discussion_id,
                message="<p>Thoughtful post about systems.</p>",
                created_at=datetime(2026, 1, 15, tzinfo=UTC),
                user_name="Example Student",
            )
        ]

    def get_course_users(
        self, course_id: int, enrollment_type: list[str] | None = None
    ) -> list[CanvasUser]:
        self._slow_call(f"courses/{course_id}/users")
        return [CanvasUser(id=2001, name="Example Student")]


class FakeSlowLLMClient:
    """Minimal slow LLM client that publishes traffic events."""

    def generate(self, *_args, **_kwargs):
        traffic.publish(traffic.TrafficEvent("llm", "example-model", "chat.completions", 0, 0))
        time.sleep(0.01)
        traffic.publish(
            traffic.TrafficEvent("llm", "example-model", "chat.completions", 200, 10)
        )
        return Mock(content="[]", total_tokens=10)


def test_auth_command_group_shows_remote_activity(monkeypatch) -> None:
    recorded_console = make_terminal_console()
    monkeypatch.setattr(auth, "console", recorded_console)
    fake_cred_manager = Mock()
    fake_cred_manager.get_token.return_value = "synthetic-token"
    monkeypatch.setattr(auth, "get_credential_manager", lambda: fake_cred_manager)
    monkeypatch.setattr(
        auth, "EnhancedCanvasAPI", lambda *_args, **_kwargs: FakeSlowCanvasClient()
    )

    result = CliRunner().invoke(app, ["auth", "test"])
    assert result.exit_code == 0, result.output

    rendered = recorded_console.export_text()
    assert "Testing Canvas API connection" in rendered
    assert "Canvas" in rendered
    assert "last 200" in rendered


def test_ta_status_shows_remote_activity(monkeypatch, tmp_path: Path) -> None:
    recorded_console = make_terminal_console()
    monkeypatch.setattr(ta, "console", recorded_console)
    token_file = tmp_path / "api.key"
    token_file.write_text("synthetic-token", encoding="utf-8")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"discussion:\n  api_token_file: {token_file}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ta, "create_canvas_client", lambda **_kwargs: FakeSlowCanvasClient())

    result = CliRunner().invoke(app, ["--config", str(config_file), "ta", "status"])
    assert result.exit_code == 0, result.output

    rendered = recorded_console.export_text()
    assert "Testing Canvas connection" in rendered
    assert "Canvas" in rendered
    assert "last 200" in rendered


def test_ta_courses_shows_remote_activity(monkeypatch) -> None:
    recorded_console = make_terminal_console()
    monkeypatch.setattr(courses, "console", recorded_console)
    monkeypatch.setattr(courses, "get_canvas_client", lambda _cfg: FakeSlowCanvasClient())

    result = CliRunner().invoke(app, ["ta", "courses", "list"])
    assert result.exit_code == 0, result.output

    rendered = recorded_console.export_text()
    assert "Fetching courses from Canvas" in rendered
    assert "Canvas" in rendered
    assert "last 200" in rendered


def test_ta_discussions_shows_remote_activity(monkeypatch) -> None:
    recorded_console = make_terminal_console()
    monkeypatch.setattr(discussions, "console", recorded_console)
    monkeypatch.setattr(
        discussions, "get_canvas_client", lambda _cfg: FakeSlowCanvasClient()
    )

    result = CliRunner().invoke(app, ["ta", "discussions", "list", "101"])
    assert result.exit_code == 0, result.output

    rendered = recorded_console.export_text()
    assert "Fetching discussions for course 101" in rendered
    assert "Canvas" in rendered
    assert "last 200" in rendered


def test_ta_assistant_shows_remote_activity(monkeypatch) -> None:
    recorded_console = make_terminal_console()
    monkeypatch.setattr(assistant, "console", recorded_console)
    monkeypatch.setattr(
        assistant, "get_canvas_client", lambda _cfg: FakeSlowCanvasClient()
    )
    monkeypatch.setattr(
        assistant, "get_llm_client", lambda _cfg, verbose=False: FakeSlowLLMClient()
    )

    class FakeRanking:
        flagged_posts = []

        def get_top_posts(self, _count: int):
            return []

    class FakeAnalyzer:
        def __init__(self, **_kwargs):
            pass

        def analyze_discussion_batch(self, **_kwargs):
            FakeSlowLLMClient().generate()
            return FakeRanking()

    monkeypatch.setattr(assistant, "PostAnalyzer", FakeAnalyzer)

    result = CliRunner().invoke(
        app,
        ["ta", "assistant", "analyze", "501", "--course", "101", "--no-check-plagiarism"],
    )
    assert result.exit_code == 0, result.output

    rendered = recorded_console.export_text()
    assert "Fetching discussion from Canvas" in rendered
    assert "Analyzing posts with LLM" in rendered
    assert "Canvas" in rendered
    assert "LLM" in rendered
    assert "last 200" in rendered


def test_ta_gradeliner_shows_remote_activity(monkeypatch, tmp_path: Path) -> None:
    recorded_console = make_terminal_console()
    monkeypatch.setattr(ta, "console", recorded_console)
    monkeypatch.setattr(ta, "load_api_key", lambda _path: "synthetic-token")
    monkeypatch.setattr(ta, "create_canvas_client", lambda *_args: FakeSlowCanvasClient())
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path / "workspace"))

    csv_file = tmp_path / "grades.csv"
    csv_file.write_text("Student,ID\nExample Student,2001\n", encoding="utf-8")

    class FakeOrchestrator:
        def __init__(
            self, canvas_client, _llm_client, course_id: int, _path: Path, export_root: Path
        ):
            assert export_root == tmp_path / "workspace" / "exports"
            canvas_client.get_course(course_id)

        def has_cached_mappings(self) -> bool:
            return True

        def prepare_grade_import(self) -> list:
            return []

    monkeypatch.setattr(ta, "GradeLinerUpper", FakeOrchestrator)

    result = CliRunner().invoke(
        app,
        ["ta", "gradeliner", "--netacad", str(csv_file), "--course", "101"],
    )
    assert result.exit_code == 0, result.output

    rendered = recorded_console.export_text()
    assert "Loading course mappings" in rendered
    assert "Canvas" in rendered
    assert "last 200" in rendered


def test_ta_attendance_warning_shows_remote_activity(monkeypatch) -> None:
    recorded_console = make_terminal_console()
    monkeypatch.setattr(ta, "console", recorded_console)
    monkeypatch.setattr(ta, "load_api_key", lambda _path: "synthetic-token")
    monkeypatch.setattr(ta, "create_canvas_client", lambda *_args: FakeSlowCanvasClient())

    def fake_find_inactive(canvas_client, course_id: int, **_kwargs):
        canvas_client.get_course_users(course_id)
        return []

    monkeypatch.setattr(ta, "find_students_inactive_for_threshold", fake_find_inactive)

    result = CliRunner().invoke(app, ["ta", "attendance-warning", "--course-id", "101"])
    assert result.exit_code == 0, result.output

    rendered = recorded_console.export_text()
    assert "Loading activity signals from Canvas" in rendered
    assert "Canvas" in rendered
    assert "last 200" in rendered
