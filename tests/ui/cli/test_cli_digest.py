"""``coursewright ta discussions digest`` is a thin wrapper over the library."""

from __future__ import annotations

import json
from unittest.mock import Mock

import requests
from typer.testing import CliRunner

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.config import AppConfig
from coursewright.ui.cli import digest as digest_cli
from coursewright.ui.cli.main import app


class FakeDigestClient:
    def get_current_user_profile(self) -> dict:
        return {"id": 99}

    def get_active_teacher_courses(self) -> list[dict]:
        return [{"id": 1, "name": "Sample Course"}]

    def get_discussion_topics(self, course_id: int) -> list[dict]:
        return [{"id": 5, "title": "Week 1", "last_reply_at": "2999-01-01T00:00:00Z",
                 "html_url": "https://canvas.example.edu/courses/1/discussion_topics/5"}]

    def get_discussion_view_record(self, course_id: int, topic_id: int) -> dict:
        return {"participants": [{"id": 1, "display_name": "Avery Sample"}],
                "view": [{"id": 10, "user_id": 1, "created_at": "2999-01-01T00:00:00Z",
                          "message": "Quillfeather, is the prompt enough?"}]}


def test_digest_writes_to_the_given_path_with_configured_names(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(digest_cli, "get_canvas_client", lambda _config: FakeDigestClient())
    out = tmp_path / "digest.md"
    result = CliRunner().invoke(
        app,
        ["--config", str(tmp_path / "config.yaml"), "ta", "discussions", "digest",
         "--out", str(out), "--instructor-name", "Quillfeather"],
    )
    assert result.exit_code == 0, result.output
    assert "STALE DIGEST" in result.output  # a post from the future trips the clock check
    assert out.exists() and out.with_suffix(".raw.md").exists()
    (post,) = json.loads(out.with_suffix(".posts.json").read_text(encoding="utf-8"))
    assert post["mentions_instructor"] is True


def test_digest_without_a_workspace_fails_loudly(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(digest_cli, "get_canvas_client", lambda _config: FakeDigestClient())
    monkeypatch.setattr(
        "coursewright.ta.discussions.daily_digest.resolve_workspace_directory", lambda: None
    )
    config_file = str(tmp_path / "config.yaml")
    result = CliRunner().invoke(app, ["--config", config_file, "ta", "discussions", "digest"])
    assert result.exit_code == 1
    assert "workspace" in result.output.lower()


def test_digest_defaults_to_the_selected_config_workspace(tmp_path, monkeypatch) -> None:
    """``--config`` picks the workspace; an unrelated ``config.yaml`` never does."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.setattr(digest_cli, "get_canvas_client", lambda _config: FakeDigestClient())
    selected_workspace = tmp_path / "selected-private"
    config_file = tmp_path / "selected.yaml"
    AppConfig(workspace=str(selected_workspace)).save_to_file(config_file)
    monkeypatch.chdir(tmp_path)  # no config.yaml here to borrow a workspace from
    result = CliRunner().invoke(
        app, ["--config", str(config_file), "ta", "discussions", "digest"]
    )
    assert result.exit_code == 0, result.output
    assert list((selected_workspace / "exports" / "digests").glob("*.posts.json"))


def test_client_digest_calls_go_through_the_shared_choke_point() -> None:
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"id": 7}'
    canvas = CanvasClient("https://canvas.example.edu", "synthetic-token")
    canvas._rate_limit_delay = 0
    canvas._session = Mock()
    canvas._session.request.return_value = response
    assert canvas.get_current_user_profile() == {"id": 7}
    assert canvas.get_discussion_view_record(1, 5) == {"id": 7}
    urls = [call.args[1] for call in canvas._session.request.call_args_list]
    assert urls == [
        "https://canvas.example.edu/api/v1/users/self",
        "https://canvas.example.edu/api/v1/courses/1/discussion_topics/5/view",
    ]
