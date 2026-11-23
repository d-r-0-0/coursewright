"""CLI smoke tests.

Verifies every CLI module imports cleanly, Typer resolves every command
and subcommand ``--help``, startup validation uses the Canvas client
factory, and core TA/prompt contracts hold.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

CLI_MODULES = [
    "coursewright.ui.cli",
    "coursewright.ui.cli.main",
    "coursewright.ui.cli.activity",
    "coursewright.ui.cli.analysis",
    "coursewright.ui.cli.assistant",
    "coursewright.ui.cli.auth",
    "coursewright.ui.cli.config_manager",
    "coursewright.ui.cli.courses",
    "coursewright.ui.cli.digest",
    "coursewright.ui.cli.discussions",
    "coursewright.ui.cli.doctor",
    "coursewright.ui.cli.gradeliner_menu",
    "coursewright.ui.cli.llm",
    "coursewright.ui.cli.llm_utils",
    "coursewright.ui.cli.packet_tracer",
    "coursewright.ui.cli.ta",
    "coursewright.ui.cli.zero_ungraded",
    "coursewright.ui.cli.zybooks_import",
]


@pytest.mark.parametrize("module_name", CLI_MODULES)
def test_cli_module_imports_cleanly(module_name: str) -> None:
    """Each CLI module can be imported without raising an exception."""
    mod = importlib.import_module(module_name)
    assert mod is not None


def test_typer_main_app_builds_command_tree() -> None:
    """Top-level Typer app resolves its full command tree without error."""
    from coursewright.ui.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"app --help failed:\n{result.output}"


COMMAND_HELP_ARGS = [
    ["tui", "--help"],
    ["menu", "--help"],
    ["interactive", "menu", "--help"],
    ["auth", "--help"],
    ["auth", "login", "--help"],
    ["auth", "migrate-to-keyring", "--help"],
    ["auth", "status", "--help"],
    ["auth", "logout", "--help"],
    ["auth", "test", "--help"],
    ["auth", "login-browser", "--help"],
    ["config", "--help"],
    ["config", "show", "--help"],
    ["config", "set", "--help"],
    ["config", "reset", "--help"],
    ["config", "export", "--help"],
    ["config", "import", "--help"],
    ["config", "validate", "--help"],
    ["ta", "--help"],
    ["ta", "start", "--help"],
    ["ta", "menu", "--help"],
    ["ta", "interactive", "menu", "--help"],
    ["ta", "status", "--help"],
    ["ta", "setup", "--help"],
    ["ta", "netacad-import", "--help"],
    ["ta", "gradeliner", "--help"],
    ["ta", "zybooks-import", "--help"],
    ["ta", "zero-ungraded", "--help"],
    ["ta", "packet-tracer", "--help"],
    ["ta", "doctor", "--help"],
    ["ta", "attendance-warning", "--help"],
    ["ta", "demo", "--help"],
    ["ta", "courses", "--help"],
    ["ta", "courses", "list", "--help"],
    ["ta", "courses", "info", "--help"],
    ["ta", "courses", "select", "--help"],
    ["ta", "courses", "search", "--help"],
    ["ta", "courses", "recent", "--help"],
    ["ta", "discussions", "--help"],
    ["ta", "discussions", "list", "--help"],
    ["ta", "discussions", "show", "--help"],
    ["ta", "discussions", "summary", "--help"],
    ["ta", "discussions", "grade", "--help"],
    ["ta", "discussions", "posts", "--help"],
    ["ta", "discussions", "submit-grades", "--help"],
    ["ta", "discussions", "export-course", "--help"],
    ["ta", "discussions", "process", "--help"],
    ["ta", "llm", "--help"],
    ["ta", "llm", "list-models", "--help"],
    ["ta", "llm", "set-default", "--help"],
    ["ta", "llm", "compare", "--help"],
    ["ta", "llm", "usage", "--help"],
    ["ta", "assistant", "--help"],
    ["ta", "assistant", "analyze", "--help"],
    ["ta", "assistant", "respond", "--help"],
    ["ta", "analyze-course", "--help"],
    ["design", "--help"],
    ["design", "rubrics", "--help"],
    ["design", "rubric", "--help"],
]


@pytest.mark.parametrize("args", COMMAND_HELP_ARGS)
def test_typer_subcommands_resolve(args: list[str]) -> None:
    """Every registered command and subcommand group can display --help."""
    from coursewright.ui.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, args)
    assert result.exit_code == 0, (
        f"'{' '.join(args)}' failed (exit {result.exit_code}):\n{result.output}"
    )


def test_ta_demo_runs_offline() -> None:
    """The offline demo command renders sample output cleanly."""
    from coursewright.ui.cli.main import app

    result = CliRunner().invoke(app, ["ta", "demo"])
    assert result.exit_code == 0
    assert "Demo Complete" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["menu", "--help"],
        ["interactive", "menu", "--help"],
        ["ta", "menu", "--help"],
        ["ta", "interactive", "menu", "--help"],
    ],
)
def test_compatibility_route_help_avoids_textual_and_canvas_work(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy-route help is safe to inspect before any local or remote setup."""
    from coursewright.ui.cli import main, ta
    from coursewright.ui.cli.main import app

    def unexpected_work(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("--help must not launch the TUI or contact Canvas")

    monkeypatch.setattr(main, "launch_tui", unexpected_work)
    monkeypatch.setattr(ta, "start", unexpected_work)
    monkeypatch.setattr(ta, "create_canvas_client", unexpected_work)

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    assert "Deprecated" in result.output


@pytest.mark.parametrize("args", [["menu"], ["interactive", "menu"]])
def test_root_menu_compatibility_routes_redirect_to_tui(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root legacy routes explain the move and delegate to ``coursewright tui``."""
    from coursewright.ui.cli import main
    from coursewright.ui.cli.main import app

    launched: list[bool] = []
    monkeypatch.setattr(main, "launch_tui", lambda _context: launched.append(True))

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    assert "deprecated" in result.output.lower()
    assert "coursewright tui" in result.output
    assert launched == [True]


@pytest.mark.parametrize(
    "args", [["ta", "menu"], ["ta", "interactive", "menu"]]
)
def test_ta_menu_compatibility_routes_redirect_to_start(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TA legacy routes explain the move and delegate to ``coursewright ta start``."""
    from coursewright.ui.cli import ta
    from coursewright.ui.cli.main import app

    started: list[bool] = []
    monkeypatch.setattr(ta, "start", lambda _context: started.append(True))

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    assert "deprecated" in result.output.lower()
    assert "coursewright ta start" in result.output
    assert started == [True]


def test_tui_command_still_launches_textual_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The current root TUI route keeps its direct launch behavior."""
    from coursewright.ui.cli.main import app

    launched: list[bool] = []
    monkeypatch.setattr(
        "coursewright.ui.tui.app.run_tui", lambda **_kwargs: launched.append(True)
    )

    result = CliRunner().invoke(app, ["tui"])

    assert result.exit_code == 0, result.output
    assert launched == [True]


def test_ta_start_still_validates_then_launches_textual_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The current TA start route keeps its validation and launch behavior."""
    from coursewright.ui.cli import ta
    from coursewright.ui.cli.main import app

    launched: list[bool] = []
    monkeypatch.setattr(ta, "_run_startup_api_validation", lambda _config: True)
    monkeypatch.setattr(
        "coursewright.ui.tui.app.run_tui", lambda **_kwargs: launched.append(True)
    )

    result = CliRunner().invoke(app, ["ta", "start"])

    assert result.exit_code == 0, result.output
    assert launched == [True]


def test_startup_validation_uses_real_canvas_client_factory_success_path() -> None:
    """Startup validation should reflect the real auth strategy in use."""
    from coursewright.ui.cli.ta import _run_startup_api_validation

    mock_config = Mock()
    mock_config.discussion.canvas_url = "https://canvas.example.edu"
    mock_config.llm.enabled = True
    mock_config.llm.get_api_key.return_value = "llm-key"

    mock_auth_strategy = Mock()
    mock_auth_strategy.describe.return_value = "Browser session (cookies)"
    mock_canvas_client = Mock()
    mock_canvas_client._auth_strategy = mock_auth_strategy

    with (
        patch("coursewright.ui.cli.ta.create_canvas_client", return_value=mock_canvas_client),
        patch("coursewright.ui.cli.ta._show_startup_validation_panel") as mock_show_panel,
    ):
        result = _run_startup_api_validation(mock_config)

    assert result is True
    mock_show_panel.assert_called_once_with(
        canvas_is_valid=True,
        canvas_message="Connected via Browser session (cookies)",
        llm_is_ready=True,
        llm_message="LLM key detected",
    )


def test_startup_validation_reports_failure_when_canvas_client_factory_fails() -> None:
    """Startup validation should fail only after the real client path fails."""
    from coursewright.ui.cli.ta import _run_startup_api_validation

    mock_config = Mock()
    mock_config.discussion.canvas_url = "https://canvas.example.edu"
    mock_config.llm.enabled = True
    mock_config.llm.get_api_key.return_value = "llm-key"

    with (
        patch("coursewright.ui.cli.ta.create_canvas_client", return_value=None),
        patch("coursewright.ui.cli.ta._show_startup_validation_panel") as mock_show_panel,
    ):
        result = _run_startup_api_validation(mock_config)

    assert result is False
    mock_show_panel.assert_called_once_with(
        canvas_is_valid=False,
        canvas_message=(
            "Could not authenticate to Canvas. Browser fallback either failed or was cancelled."
        ),
        llm_is_ready=True,
        llm_message="LLM key detected",
    )


CORE_MODULES = [
    "coursewright.core.canvas.models",
    "coursewright.core.canvas.client",
    "coursewright.ta.grading.analyzer",
    "coursewright.ta.grading.gradebook",
    "coursewright.core.llm.client",
    "coursewright.ta.grading.feedback_generator",
    "coursewright.ta.discussions.post_analyzer",
    "coursewright.ta.discussions.response_generator",
    "coursewright.core.export.markdown_exporter",
]


@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_core_module_imports_cleanly(module_name: str) -> None:
    """Core and TA library modules import without raising exceptions."""
    mod = importlib.import_module(module_name)
    assert mod is not None


def _feedback_user_prompt_path() -> Path:
    import coursewright

    return (
        Path(coursewright.__file__).parent
        / "core"
        / "llm"
        / "prompts"
        / "FEEDBACK_USER.md"
    )


def test_feedback_user_prompt_has_post_content_placeholder() -> None:
    """FEEDBACK_USER.md includes {post_content} so student writing reaches the LLM."""
    template_path = _feedback_user_prompt_path()
    assert template_path.exists(), f"Template not found: {template_path}"
    content = template_path.read_text(encoding="utf-8")
    assert "{post_content}" in content


REQUIRED_PLACEHOLDERS = [
    "{student_name}",
    "{total_score}",
    "{word_count}",
    "{reply_count}",
    "{post_content}",
    "{discussion_context}",
]


@pytest.mark.parametrize("placeholder", REQUIRED_PLACEHOLDERS)
def test_feedback_template_has_required_placeholder(placeholder: str) -> None:
    """Feedback user prompt template includes all required placeholders."""
    content = _feedback_user_prompt_path().read_text(encoding="utf-8")
    assert placeholder in content, f"Missing placeholder {placeholder} in FEEDBACK_USER.md"


def test_analyzer_stores_text_snippets_in_post_details() -> None:
    """DiscussionAnalyzer stores cleaned text_snippet in post_details."""
    from coursewright.core.canvas.models import CanvasDiscussionEntry, CanvasUser
    from coursewright.ta.grading.analyzer import DiscussionAnalyzer

    mock_client = Mock()
    grading_config = {
        "min_words": 300,
        "min_replies": 2,
        "rubric_file": None,
        "substantial_post_points": 60.0,
        "reply_points_each": 20.0,
        "max_replies_counted": 2,
    }

    analyzer = DiscussionAnalyzer(mock_client, grading_config)
    post = CanvasDiscussionEntry(
        id=1,
        user_id=42,
        discussion_id=100,
        message="<p>This is my thoughtful discussion post about networking.</p>",
        created_at=datetime(2026, 1, 15),
        user_name="Test Student",
    )
    user = CanvasUser(id=42, name="Test Student")
    result = analyzer._analyze_individual_participation(user, [post])

    assert len(result.post_details) == 1
    detail = result.post_details[0]
    assert "text_snippet" in detail
    assert "networking" in detail["text_snippet"]
    assert "<p>" not in detail["text_snippet"]
