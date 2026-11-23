"""Unit tests for ``coursewright.ui.cli.doctor`` and ``connect_canvas_from_config``."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import typer
from typer.testing import CliRunner

from coursewright.core.canvas import connect_canvas_from_config as exported_connect_canvas
from coursewright.core.canvas.client import connect_canvas_from_config
from coursewright.core.config import AppConfig
from coursewright.ui.cli import doctor as doctor_cli
from coursewright.ui.cli.doctor import REQUIRED_TEMPLATE_FILES, doctor_command


def _build_doctor_app(config: AppConfig) -> typer.Typer:
    """Create a standalone Typer app exposing ``doctor_command`` with ``config``."""
    test_app = typer.Typer()

    @test_app.callback()
    def _inject_config(ctx: typer.Context) -> None:
        ctx.obj = {"config": config}

    test_app.command("doctor")(doctor_command)
    return test_app


def _make_healthy_workspace(base_dir: Path, *, include_policies: bool = True) -> Path:
    """Populate a temporary workspace with templates and optional policies."""
    workspace = base_dir / "workspace"
    templates_dir = workspace / "templates"
    templates_dir.mkdir(parents=True)
    for filename in REQUIRED_TEMPLATE_FILES:
        (templates_dir / filename).write_text(f"Template: {filename}\n", encoding="utf-8")
    if include_policies:
        (workspace / "course-policies.yaml").write_text("courses: {}\n", encoding="utf-8")
    return workspace


def _make_fake_canvas_client(auth_label: str = "API token (Bearer)") -> Mock:
    """Return a mock Canvas client with a safe ``_auth_strategy.describe()``."""
    auth_strategy = Mock()
    auth_strategy.describe.return_value = auth_label
    client = Mock()
    client._auth_strategy = auth_strategy
    return client


def test_connect_canvas_from_config_delegates_settings() -> None:
    """``connect_canvas_from_config`` reads discussion URL and token file via duck-typing."""
    assert exported_connect_canvas is connect_canvas_from_config

    config = AppConfig()
    config.discussion.canvas_url = "https://canvas.example.edu"
    config.discussion.api_token_file = "/tmp/synthetic.key"
    fake_client = _make_fake_canvas_client()

    with patch(
        "coursewright.core.canvas.client.create_canvas_client",
        return_value=fake_client,
    ) as mock_factory:
        result = connect_canvas_from_config(config)

    assert result is fake_client
    mock_factory.assert_called_once_with(
        base_url="https://canvas.example.edu",
        api_token_file="/tmp/synthetic.key",
    )


def test_connect_canvas_from_config_handles_none_config() -> None:
    """``connect_canvas_from_config`` handles missing config objects cleanly."""
    with patch(
        "coursewright.core.canvas.client.create_canvas_client",
        return_value=None,
    ) as mock_factory:
        result = connect_canvas_from_config(None)

    assert result is None
    mock_factory.assert_called_once_with(base_url=None, api_token_file=None)


def test_doctor_healthy_json_output_without_leaking_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Healthy environment emits clean JSON with OK checks and never exposes API keys."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    workspace = _make_healthy_workspace(tmp_path)

    secret_llm_key = "sk-synthetic-super-secret-value"  # pragma: allowlist secret
    config = AppConfig(workspace=str(workspace))
    config.discussion.canvas_url = "https://canvas.example.edu"
    config.llm.enabled = True
    config.llm.api_key = secret_llm_key

    monkeypatch.setattr(
        doctor_cli,
        "connect_canvas_from_config",
        lambda _cfg: _make_fake_canvas_client("API token (Bearer)"),
    )

    test_app = _build_doctor_app(config)
    result = CliRunner().invoke(test_app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    assert secret_llm_key not in result.output

    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["workspace"]["status"] == "OK"
    assert payload["workspace"]["writable"] is True
    assert payload["templates"]["status"] == "OK"
    assert payload["templates"]["missing"] == []
    assert payload["course_policies"]["status"] == "OK"
    assert payload["course_policies"]["present"] is True
    assert payload["canvas"]["status"] == "OK"
    assert payload["canvas"]["connected"] is True
    assert payload["canvas"]["auth_mode"] == "API token (Bearer)"
    assert payload["llm"]["enabled"] is True
    assert payload["llm"]["api_key_configured"] is True
    assert payload["llm"]["status"] == "OK"
    assert {check["status"] for check in payload["checks"]} == {"OK"}


def test_doctor_healthy_rich_table_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-JSON mode renders a Rich table showing OK / WARN / FAIL checks."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    workspace = _make_healthy_workspace(tmp_path, include_policies=False)

    config = AppConfig(workspace=str(workspace))
    config.discussion.canvas_url = "https://canvas.example.edu"
    config.llm.enabled = False

    monkeypatch.setattr(
        doctor_cli,
        "connect_canvas_from_config",
        lambda _cfg: _make_fake_canvas_client("Browser session (cookies)"),
    )

    test_app = _build_doctor_app(config)
    result = CliRunner().invoke(test_app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "Coursewright Doctor" in result.output
    assert "Workspace" in result.output
    assert "Templates" in result.output
    assert "Course Policies" in result.output
    assert "Canvas API" in result.output
    assert "LLM" in result.output
    assert "OK" in result.output
    assert "WARN" in result.output
    assert "FAIL" not in result.output


def test_doctor_missing_workspace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing or unconfigured workspace reports FAIL and exits with code 1."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    missing_dir = tmp_path / "does-not-exist"
    config = AppConfig(workspace=str(missing_dir))

    monkeypatch.setattr(
        doctor_cli,
        "connect_canvas_from_config",
        lambda _cfg: _make_fake_canvas_client(),
    )

    test_app = _build_doctor_app(config)
    result = CliRunner().invoke(test_app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["status"] == "fail"
    assert payload["workspace"]["status"] == "FAIL"
    assert payload["workspace"]["exists"] is False
    assert payload["workspace"]["writable"] is False


def test_doctor_unconfigured_workspace_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no workspace is configured anywhere, doctor exits with code 1."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    config = AppConfig(workspace=None)

    monkeypatch.setattr(
        doctor_cli,
        "connect_canvas_from_config",
        lambda _cfg: _make_fake_canvas_client(),
    )

    test_app = _build_doctor_app(config)
    result = CliRunner().invoke(test_app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["workspace"]["status"] == "FAIL"
    assert payload["workspace"]["path"] is None


def test_doctor_unwritable_workspace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing but unwritable workspace fails preflight with exit code 1."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    workspace = _make_healthy_workspace(tmp_path)
    config = AppConfig(workspace=str(workspace))

    monkeypatch.setattr(doctor_cli.os, "access", lambda _path, _mode: False)
    monkeypatch.setattr(
        doctor_cli,
        "connect_canvas_from_config",
        lambda _cfg: _make_fake_canvas_client(),
    )

    test_app = _build_doctor_app(config)
    result = CliRunner().invoke(test_app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["workspace"]["status"] == "FAIL"
    assert payload["workspace"]["exists"] is True
    assert payload["workspace"]["writable"] is False


def test_doctor_missing_templates_warns_without_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing template files produce a WARN check listing missing templates."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    workspace = tmp_path / "workspace"
    templates_dir = workspace / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "missing_submission.md").write_text("hi\n", encoding="utf-8")
    (workspace / "course-policies.yaml").write_text("courses: {}\n", encoding="utf-8")

    config = AppConfig(workspace=str(workspace))
    monkeypatch.setattr(
        doctor_cli,
        "connect_canvas_from_config",
        lambda _cfg: _make_fake_canvas_client(),
    )

    test_app = _build_doctor_app(config)
    result = CliRunner().invoke(test_app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["status"] == "warn"
    assert payload["templates"]["status"] == "WARN"
    assert payload["templates"]["present"] == ["missing_submission.md"]
    assert payload["templates"]["missing"] == [
        "missing_lab_document.md",
        "missing_packet_tracer.md",
    ]


@pytest.mark.parametrize(
    "canvas_outcome",
    [None, RuntimeError("Canvas connection timed out")],
)
def test_doctor_failed_canvas_connection_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    canvas_outcome: object,
) -> None:
    """Canvas returning None or raising an error reports FAIL and exits with code 1."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    workspace = _make_healthy_workspace(tmp_path)
    config = AppConfig(workspace=str(workspace))

    def fake_connect(_cfg: object) -> object:
        if isinstance(canvas_outcome, Exception):
            raise canvas_outcome
        return canvas_outcome

    monkeypatch.setattr(doctor_cli, "connect_canvas_from_config", fake_connect)

    test_app = _build_doctor_app(config)
    result = CliRunner().invoke(test_app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["status"] == "fail"
    assert payload["canvas"]["status"] == "FAIL"
    assert payload["canvas"]["connected"] is False
    if isinstance(canvas_outcome, Exception):
        assert "Canvas connection timed out" in payload["canvas"]["detail"]


def test_doctor_llm_enabled_without_key_warns_without_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled LLM without an API key emits WARN without exiting non-zero."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    workspace = _make_healthy_workspace(tmp_path)

    config = AppConfig(workspace=str(workspace))
    config.llm.enabled = True
    config.llm.api_key = None
    config.llm.api_key_file = None

    monkeypatch.setattr(
        doctor_cli,
        "connect_canvas_from_config",
        lambda _cfg: _make_fake_canvas_client(),
    )

    test_app = _build_doctor_app(config)
    result = CliRunner().invoke(test_app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["llm"]["enabled"] is True
    assert payload["llm"]["api_key_configured"] is False
    assert payload["llm"]["status"] == "WARN"


def test_doctor_handles_duck_typed_config_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doctor tolerates minimal config objects via safe getattr access."""
    workspace = _make_healthy_workspace(tmp_path)
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(workspace))

    minimal_config = SimpleNamespace(
        workspace_directory=str(workspace),
        discussion=SimpleNamespace(
            canvas_url="https://canvas.example.edu",
            api_token_file="api.key",
        ),
        llm=SimpleNamespace(
            enabled=False,
            provider="openai",
            model="gpt-4o-mini",
            get_api_key=lambda: "",
        ),
    )

    test_app = _build_doctor_app(minimal_config)  # type: ignore[arg-type]
    monkeypatch.setattr(
        doctor_cli,
        "connect_canvas_from_config",
        lambda _cfg: _make_fake_canvas_client(),
    )

    result = CliRunner().invoke(test_app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["canvas"]["connected"] is True
