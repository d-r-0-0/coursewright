"""Unit tests for ``coursewright.ui.cli.zero_ungraded``."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import typer
from typer.testing import CliRunner

from coursewright.core.canvas.models import Assignment, CanvasUser, Submission
from coursewright.core.canvas.safe_write import default_backup_directory
from coursewright.core.config import AppConfig
from coursewright.ui.cli import zero_ungraded as zero_cli


def _build_test_app(config: AppConfig | None = None) -> typer.Typer:
    cli = typer.Typer()

    @cli.callback()
    def _configure(ctx: typer.Context) -> None:
        ctx.obj = {"config": config or AppConfig()}

    cli.command("zero-ungraded")(zero_cli.zero_ungraded_command)
    return cli


def _make_fake_client() -> MagicMock:
    now = datetime.now(UTC)
    client = MagicMock()
    client.get_course_users.return_value = [
        CanvasUser(id=101, name="Alex Rivera"),
        CanvasUser(id=102, name="Jordan Patel"),
        CanvasUser(id=103, name="Morgan Lee"),
    ]
    client.get_assignments.return_value = [
        Assignment(id=1, name="Past Lab", due_at=now - timedelta(days=2), published=True),
        Assignment(id=2, name="All Ungraded Lab", due_at=now - timedelta(days=1), published=True),
        Assignment(id=3, name="Future Lab", due_at=now + timedelta(days=3), published=True),
        Assignment(id=4, name="Hidden Lab", due_at=now - timedelta(days=4), published=False),
    ]
    submissions = {
        1: [
            Submission(id=10, user_id=101, score=90.0, workflow_state="graded"),
            Submission(id=11, user_id=102, score=None, workflow_state="submitted"),
            Submission(id=12, user_id=103, score=None, workflow_state="unsubmitted"),
            Submission(id=13, user_id=999, score=None, workflow_state="unsubmitted"),
        ],
        2: [
            Submission(id=20, user_id=101, score=None, workflow_state="unsubmitted"),
        ],
        3: [
            Submission(id=30, user_id=101, score=None, workflow_state="unsubmitted"),
        ],
    }
    client.get_assignment_submissions.side_effect = (
        lambda _course_id, assignment_id: submissions.get(assignment_id, [])
    )
    client.get_submission.return_value = {"score": None}
    client.submit_grade.return_value = True
    return client


def test_cli_dry_run_rich_shows_table_guardrails_and_never_writes(monkeypatch) -> None:
    client = _make_fake_client()
    captured_args: dict[str, object] = {}

    def _fake_create_client(*, base_url=None, api_token_file=None):
        captured_args["base_url"] = base_url
        captured_args["api_token_file"] = api_token_file
        return client

    monkeypatch.setattr(zero_cli, "create_canvas_client", _fake_create_client)
    config = AppConfig()
    cli = _build_test_app(config)

    result = CliRunner().invoke(cli, ["zero-ungraded", "--course", "123"])

    assert result.exit_code == 0, result.output
    assert captured_args == {
        "base_url": config.discussion.canvas_url,
        "api_token_file": config.discussion.api_token_file,
    }
    assert "Past Lab" in result.output
    assert "All Ungraded Lab" in result.output
    assert "Submitted Work Skipped" in result.output
    assert "All-Ungraded Guardrail" in result.output
    assert "DRY RUN" in result.output
    client.submit_grade.assert_not_called()


def test_cli_json_dry_run_emits_clean_json_without_activity_display(monkeypatch) -> None:
    client = _make_fake_client()
    monkeypatch.setattr(zero_cli, "create_canvas_client", lambda **_kwargs: client)

    def _forbid_spinner(*_args, **_kwargs):
        raise AssertionError("RemoteActivityDisplay must not run in --json mode")

    monkeypatch.setattr(zero_cli, "RemoteActivityDisplay", _forbid_spinner)
    cli = _build_test_app()

    result = CliRunner().invoke(cli, ["zero-ungraded", "-c", "123", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["course_id"] == 123
    assert payload["executed"] is False
    assert payload["skipped_unpublished"] == 1
    assert payload["total_targets"] == 2
    assert payload["total_skipped_submitted"] == 1
    assert payload["total_skipped_inactive_or_test"] == 1
    assert payload["all_ungraded_assignment_ids"] == [2]
    assert len(payload["assignments"]) == 2
    assert len(payload["warnings"]) == 2
    client.submit_grade.assert_not_called()


def test_cli_json_supports_all_assignment_filter_and_include_submitted(monkeypatch) -> None:
    client = _make_fake_client()
    monkeypatch.setattr(zero_cli, "create_canvas_client", lambda **_kwargs: client)
    cli = _build_test_app()

    result = CliRunner().invoke(
        cli,
        [
            "zero-ungraded",
            "--course",
            "123",
            "--all",
            "--assignment",
            "1",
            "--assignment",
            "3",
            "--include-submitted",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["past_due_only"] is False
    assert payload["include_submitted"] is True
    assert [item["assignment_id"] for item in payload["assignments"]] == [1, 3]
    assert payload["total_targets"] == 3
    assert payload["total_skipped_submitted"] == 0


def test_cli_execute_requires_workspace_in_rich_and_json_modes(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    client = _make_fake_client()
    monkeypatch.setattr(zero_cli, "create_canvas_client", lambda **_kwargs: client)
    cli = _build_test_app(AppConfig())

    rich_result = CliRunner().invoke(cli, ["zero-ungraded", "--course", "123", "--execute"])
    assert rich_result.exit_code == 1
    assert "workspace" in rich_result.output.lower()
    client.submit_grade.assert_not_called()

    json_result = CliRunner().invoke(
        cli, ["zero-ungraded", "--course", "123", "--execute", "--json"]
    )
    assert json_result.exit_code == 1
    err_payload = json.loads(json_result.stdout)
    assert err_payload["executed"] is False
    assert "workspace" in err_payload["error"].lower()
    client.submit_grade.assert_not_called()


def test_cli_execute_writes_backups_and_zeros_active_unsubmitted_targets(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(workspace))
    client = _make_fake_client()
    monkeypatch.setattr(zero_cli, "create_canvas_client", lambda **_kwargs: client)
    cli = _build_test_app(AppConfig(workspace=str(workspace)))

    result = CliRunner().invoke(cli, ["zero-ungraded", "--course", "123", "--execute"])

    assert result.exit_code == 0, result.output
    expected_backup_dir = default_backup_directory(workspace)
    assert "Zeroed 2 submission(s) across 2 assignment(s)." in result.output
    assert str(expected_backup_dir) in result.output
    assert sorted(call.args for call in client.submit_grade.call_args_list) == [
        (123, 1, 103, 0, None),
        (123, 2, 101, 0, None),
    ]
    assert len(list(expected_backup_dir.glob("*.json"))) == 2


def test_cli_execute_json_reports_counts_and_backup_directory(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(workspace))
    client = _make_fake_client()
    monkeypatch.setattr(zero_cli, "create_canvas_client", lambda **_kwargs: client)
    cli = _build_test_app(AppConfig(workspace=str(workspace)))

    result = CliRunner().invoke(
        cli,
        ["zero-ungraded", "--course", "123", "--assignment", "1", "--execute", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["executed"] is True
    assert payload["total_zeroed"] == 1
    assert payload["assignments_touched"] == 1
    assert payload["backup_directory"] == str(default_backup_directory(workspace))
    client.submit_grade.assert_called_once_with(123, 1, 103, 0, None)


def test_cli_handles_canvas_connection_and_write_failures_cleanly(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(zero_cli, "create_canvas_client", lambda **_kwargs: None)
    cli = _build_test_app()

    rich_conn_err = CliRunner().invoke(cli, ["zero-ungraded", "--course", "123"])
    assert rich_conn_err.exit_code == 1
    assert "Canvas" in rich_conn_err.output

    json_conn_err = CliRunner().invoke(cli, ["zero-ungraded", "--course", "123", "--json"])
    assert json_conn_err.exit_code == 1
    assert "error" in json.loads(json_conn_err.stdout)

    def _boom(**_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(zero_cli, "create_canvas_client", _boom)
    json_exc_err = CliRunner().invoke(cli, ["zero-ungraded", "--course", "123", "--json"])
    assert json_exc_err.exit_code == 1
    assert "network down" in json.loads(json_exc_err.stdout)["error"]

    workspace = tmp_path / "workspace"
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(workspace))
    client = _make_fake_client()
    client.submit_grade.return_value = False
    monkeypatch.setattr(zero_cli, "create_canvas_client", lambda **_kwargs: client)
    cli_with_ws = _build_test_app(AppConfig(workspace=str(workspace)))

    write_fail = CliRunner().invoke(
        cli_with_ws, ["zero-ungraded", "--course", "123", "--execute", "--json"]
    )
    assert write_fail.exit_code == 1
    assert "did not accept" in json.loads(write_fail.stdout)["error"]
