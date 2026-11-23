"""Authentication and selected configuration regressions, without live services."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from coursewright.core.auth import StorageMethod
from coursewright.core.canvas.models import DiscussionAnalysis, StudentParticipation
from coursewright.core.config import AppConfig
from coursewright.ui.cli import assistant, auth, courses, design, discussions
from coursewright.ui.cli.main import app


@pytest.mark.parametrize("module", [assistant, courses, design, discussions])
def test_canvas_commands_use_shared_credential_resolution(module, tmp_path, monkeypatch):
    config = AppConfig()
    config.discussion.api_token_file = str(tmp_path / "absent.key")
    factory = Mock(return_value=object())
    monkeypatch.setattr(module, "create_canvas_client", factory)

    assert module.get_canvas_client(config) is factory.return_value

    factory.assert_called_once_with(
        base_url=config.discussion.canvas_url,
        api_token_file=config.discussion.api_token_file,
    )


@pytest.mark.parametrize("route", [["tui"], ["menu"], ["interactive", "menu"]])
def test_tui_routes_keep_selected_configuration(route, tmp_path, monkeypatch):
    selected_file = tmp_path / "selected.yaml"
    config = AppConfig(workspace=str(tmp_path / "private"))
    config.save_to_file(selected_file)
    launch = Mock()
    monkeypatch.setattr("coursewright.ui.tui.app.run_tui", launch)

    result = CliRunner().invoke(app, ["--config", str(selected_file), *route])

    assert result.exit_code == 0, result.output
    assert launch.call_args.kwargs["config_file"] == str(selected_file)
    assert launch.call_args.kwargs["config"].workspace_directory == config.workspace_directory


def test_ta_start_launches_with_selected_configuration(tmp_path, monkeypatch):
    from coursewright.ui.cli import ta

    selected_file = tmp_path / "selected.yaml"
    config = AppConfig(workspace=str(tmp_path / "private"))
    config.save_to_file(selected_file)
    launch = Mock()
    monkeypatch.setattr(ta, "_run_startup_api_validation", lambda _config: True)
    monkeypatch.setattr("coursewright.ui.tui.app.run_tui", launch)

    result = CliRunner().invoke(app, ["--config", str(selected_file), "ta", "start"])

    assert result.exit_code == 0, result.output
    assert launch.call_args.kwargs["config_file"] == str(selected_file)
    assert launch.call_args.kwargs["config"].workspace_directory == config.workspace_directory


@pytest.mark.parametrize("contents", ["discussion: [", "[]", "false"])
def test_invalid_configuration_stops_before_tui_launch(contents, tmp_path, monkeypatch):
    selected_file = tmp_path / "invalid.yaml"
    selected_file.write_text(contents)
    launch = Mock()
    monkeypatch.setattr("coursewright.ui.tui.app.run_tui", launch)

    result = CliRunner().invoke(app, ["--config", str(selected_file), "tui"])

    assert result.exit_code == 1
    assert "Invalid configuration" in result.output
    launch.assert_not_called()
    assert selected_file.read_text() == contents


def test_assistant_uses_configured_llm_key_and_temperature(monkeypatch):
    config = AppConfig()
    config.llm.enabled = True
    config.llm.api_key = "synthetic-key"  # pragma: allowlist secret
    config.llm.temperature = 0.25
    client = Mock()
    monkeypatch.setattr(assistant, "LLMClient", client)

    assistant.get_llm_client(config)

    settings = client.call_args.args[0]
    assert settings.api_key == config.llm.api_key
    assert settings.temperature == 0.25


def test_assistant_saves_response_using_dictionary_course(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    canvas = Mock()
    canvas.get_discussion_topic.return_value = SimpleNamespace(title="Topic")
    canvas.get_discussion_entries.return_value = [SimpleNamespace(id=7)]
    canvas.get_course.return_value = {"id": 12, "name": "Example Course"}
    response = SimpleNamespace(
        final_content="Response", student_name="Learner", word_count=1,
        get_markdown=lambda: "# Response\n",
    )
    generator = Mock()
    generator.generate_response.return_value = response
    monkeypatch.setattr(assistant, "get_canvas_client", lambda config: canvas)
    monkeypatch.setattr(assistant, "get_llm_client", lambda config, **kwargs: object())
    monkeypatch.setattr(assistant, "ResponseGenerator", lambda **kwargs: generator)

    result = CliRunner().invoke(
        app, ["ta", "assistant", "respond", "2", "--course", "12", "--post", "7"],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    saved = list((tmp_path / "exports").rglob("professor_response.md"))
    assert len(saved) == 1
    assert saved[0].read_text() == "# Response\n"


def test_browser_only_logout_deletes_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    credential_manager = Mock()
    credential_manager.get_storage_method.return_value = StorageMethod.NONE
    credential_manager.file_path = tmp_path / "absent.key"
    credential_manager.delete_token.return_value = (True, "No API token")
    session_manager = Mock()
    session_manager.get_storage_method.return_value = StorageMethod.FILE
    session_manager.delete_session.return_value = (True, "Session removed")
    monkeypatch.setattr(auth, "get_credential_manager", lambda: credential_manager)
    monkeypatch.setattr(auth, "get_browser_session_manager", lambda: session_manager)

    result = CliRunner().invoke(app, ["auth", "logout"], input="y\n")

    assert result.exit_code == 0, result.output
    session_manager.delete_session.assert_called_once_with()
    assert "Logged out successfully" in result.output


def test_submit_grades_uses_configured_profile_and_thresholds(tmp_path, monkeypatch):
    selected_file = tmp_path / "selected.yaml"
    config = AppConfig(workspace=str(tmp_path / "private"))
    config.discussion.default_min_words = 275
    config.discussion.default_min_replies = 3
    config.discussion.default_grading_profile = "auto"
    config.llm.enabled = False
    config.save_to_file(selected_file)

    client = Mock()
    client.get_discussion_topic.return_value = SimpleNamespace(
        id=2001, title="Topic", lock_at=None
    )
    monkeypatch.setattr(discussions, "get_canvas_client", lambda _cfg: client)
    monkeypatch.setattr(
        discussions.GradebookManager,
        "get_assignment_for_discussion",
        lambda _self, _course_id, _discussion_id: {
            "id": 3001,
            "name": "Topic",
            "points_possible": 50,
        },
    )

    captured: dict[str, object] = {}

    class FakeAnalyzer:
        def __init__(self, _client, grading_config):
            captured.update(grading_config)

        def analyze_discussion(self, course_id, discussion_id):
            return DiscussionAnalysis(
                discussion_id=discussion_id,
                discussion_title="Topic",
                course_id=course_id,
                total_students=1,
                participating_students=1,
                average_score=80.0,
                students_meeting_requirements=1,
                participation_rate=100.0,
                rubric_points=100.0,
                student_participation=[
                    StudentParticipation(
                        user_id=1,
                        user_name="Student One",
                        total_score=80.0,
                        meets_requirements=True,
                        participation_score=60.0,
                        quality_score=20.0,
                        total_word_count=320,
                        reply_count=2,
                        total_posts=3,
                        initial_post_word_count=280,
                    )
                ],
            )

    monkeypatch.setattr(discussions, "DiscussionAnalyzer", FakeAnalyzer)

    result = CliRunner().invoke(
        app,
        [
            "--config",
            str(selected_file),
            "ta",
            "discussions",
            "submit-grades",
            "2001",
            "-c",
            "1001",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert captured["min_words"] == 275
    assert captured["min_replies"] == 3
    assert captured["profile"] == "auto"

    captured.clear()
    override_result = CliRunner().invoke(
        app,
        [
            "--config",
            str(selected_file),
            "ta",
            "discussions",
            "submit-grades",
            "2001",
            "-c",
            "1001",
            "--profile",
            "legacy",
        ],
    )

    assert override_result.exit_code == 0, override_result.output
    assert captured["profile"] == "legacy"


def test_submit_grades_shows_full_comments_and_passes_grading_profile(tmp_path, monkeypatch):
    selected_file = tmp_path / "selected.yaml"
    config = AppConfig(workspace=str(tmp_path / "private"))
    config.llm.enabled = True
    config.save_to_file(selected_file)

    client = Mock()
    client.get_discussion_topic.return_value = SimpleNamespace(
        id=2001, title="Topic", message="Prompt", lock_at=None
    )
    monkeypatch.setattr(discussions, "get_canvas_client", lambda _cfg: client)
    monkeypatch.setattr(
        discussions.GradebookManager,
        "get_assignment_for_discussion",
        lambda _self, _course_id, _discussion_id: {
            "id": 3001,
            "name": "Topic",
            "points_possible": 100,
        },
    )

    class FakeAnalyzer:
        def __init__(self, _client, _grading_config):
            pass

        def analyze_discussion(self, course_id, discussion_id):
            return DiscussionAnalysis(
                discussion_id=discussion_id,
                discussion_title="Topic",
                course_id=course_id,
                total_students=1,
                participating_students=0,
                average_score=0.0,
                students_meeting_requirements=0,
                participation_rate=0.0,
                grading_profile="ai_discussion",
                rubric_points=100.0,
                student_participation=[
                    StudentParticipation(
                        user_id=1,
                        user_name="Zero Student",
                        total_score=0.0,
                        meets_requirements=False,
                        post_details=[],
                    )
                ],
            )

    monkeypatch.setattr(discussions, "DiscussionAnalyzer", FakeAnalyzer)

    captured_rubric: dict[str, object] = {}
    long_feedback = (
        "Detailed instructor feedback exceeding fifty characters so we confirm "
        "TAIL_MARKER_VERIFIED appears in preview."
    )

    def fake_generate_student_feedback(**kwargs):
        captured_rubric.update(kwargs["rubric_info"])
        return long_feedback

    fake_generator = SimpleNamespace(generate_student_feedback=fake_generate_student_feedback)
    monkeypatch.setattr(
        discussions,
        "create_feedback_generator",
        lambda _cfg, **_kwargs: (fake_generator, "openai", "gpt-5.6-terra"),
    )

    result = CliRunner().invoke(
        app,
        [
            "--config",
            str(selected_file),
            "ta",
            "discussions",
            "submit-grades",
            "2001",
            "-c",
            "1001",
            "--llm",
            "--no-llm-batch",
        ],
        env={"COLUMNS": "160"},
    )

    assert result.exit_code == 0, result.output
    normalized = " ".join(result.output.split())
    assert "TAIL_MARKER_VERIFIED" in normalized
    assert "Post 1 (Research): 0.0/30 points (0 words)" in normalized
    assert captured_rubric["grading_profile"] == "ai_discussion"
