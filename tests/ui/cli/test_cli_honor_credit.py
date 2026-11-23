"""Unit tests for ``coursewright.ui.cli.honor_credit`` (synthetic data only)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import typer
from typer.testing import CliRunner

from coursewright.core.canvas.models import Assignment, CanvasUser, Submission
from coursewright.core.canvas.safe_write import default_backup_directory
from coursewright.core.config import AppConfig
from coursewright.ui.cli import honor_credit as honor_cli


def build_test_app(config: AppConfig | None = None) -> typer.Typer:
    cli = typer.Typer()

    @cli.callback()
    def _configure(ctx: typer.Context) -> None:
        ctx.obj = {"config": config or AppConfig()}

    cli.command("honor-credit")(honor_cli.honor_credit_command)
    return cli


def make_fake_client() -> MagicMock:
    client = MagicMock()
    client.get_course_users.return_value = [
        CanvasUser(id=101, name="Alex Rivera"),
        CanvasUser(id=102, name="Jordan Patel"),
        CanvasUser(id=103, name="Morgan Lee"),
    ]
    client.get_assignments.return_value = [
        Assignment(id=1, name="U01Q01: Module Quiz", points_possible=50.0),
        Assignment(id=2, name="U01T01: Checkpoint Exam", points_possible=200.0),
    ]
    submissions = {
        1: [
            Submission(id=10, user_id=101, score=None, workflow_state="submitted"),
            Submission(id=11, user_id=102, score=0.0, workflow_state="submitted"),
            Submission(id=12, user_id=103, score=None, workflow_state="unsubmitted"),
        ],
        2: [Submission(id=20, user_id=101, score=None, workflow_state="submitted")],
    }
    client.get_assignment_submissions.side_effect = (
        lambda _course_id, assignment_id: submissions.get(assignment_id, [])
    )
    client.get_submission.return_value = {"score": None}
    client.submit_grade.return_value = True
    return client


def invoke(monkeypatch, arguments, *, client=None, config=None):
    fake_client = client or make_fake_client()
    monkeypatch.setattr(honor_cli, "create_canvas_client", lambda **_kw: fake_client)
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    result = CliRunner().invoke(build_test_app(config), ["honor-credit", *arguments])
    return result, fake_client


def test_dry_run_is_the_default_and_writes_nothing(monkeypatch, tmp_path) -> None:
    config = AppConfig(workspace=str(tmp_path / "workspace"))
    result, client = invoke(monkeypatch, ["-c", "42", "--match", "Module Quiz"], config=config)

    assert result.exit_code == 0, result.output
    assert "Honor Credit Preview" in result.output
    assert "DRY RUN" in result.output
    assert "2 submission(s) would receive" in result.output
    assert "Late Submissions Included" in result.output
    client.submit_grade.assert_not_called()
    assert not (tmp_path / "workspace" / "backups").exists()


def test_json_preview_lists_every_credit_row(monkeypatch) -> None:
    result, client = invoke(
        monkeypatch, ["-c", "42", "--match", "module quiz", "--points", "50", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["executed"] is False
    assert payload["total_credit_rows"] == 2
    assert payload["total_replacing_existing_score"] == 1
    assert [item["assignment_id"] for item in payload["assignments"]] == [1]
    rows = payload["assignments"][0]["credit_rows"]
    assert [(row["user_id"], row["previous_score"]) for row in rows] == [(101, None), (102, 0.0)]
    assert payload["assignments"][0]["not_submitted_count"] == 1
    client.submit_grade.assert_not_called()


def test_new_only_excludes_resubmissions_with_a_score(monkeypatch) -> None:
    result, _ = invoke(monkeypatch, ["-c", "42", "-a", "1", "--new-only", "--json"])

    payload = json.loads(result.output)
    assert payload["total_credit_rows"] == 1
    assert payload["assignments"][0]["skipped_existing_score"] == 1


def test_selection_is_required_before_canvas_is_contacted(monkeypatch) -> None:
    result, client = invoke(monkeypatch, ["-c", "42", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "--match" in payload["error"]
    assert payload["executed"] is False
    client.get_assignments.assert_not_called()


def test_execute_requires_a_workspace(monkeypatch) -> None:
    result, client = invoke(monkeypatch, ["-c", "42", "-a", "1", "--execute", "--json"])

    assert result.exit_code == 1
    assert "workspace" in json.loads(result.output)["error"].lower()
    client.submit_grade.assert_not_called()


def test_execute_writes_scores_comment_and_backups(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AppConfig(workspace=str(workspace))
    result, client = invoke(
        monkeypatch,
        ["-c", "42", "-a", "1", "--comment", "Completion credit.", "--execute", "--json"],
        config=config,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["executed"] is True
    assert payload["scores_written"] == 2
    assert payload["assignments_touched"] == 1
    assert [call.args for call in client.submit_grade.call_args_list] == [
        (42, 1, 101, 50.0, "Completion credit."),
        (42, 1, 102, 50.0, "Completion credit."),
    ]
    backup_directory = default_backup_directory(workspace)
    assert payload["backup_directory"] == str(backup_directory)
    assert len(list(backup_directory.glob("*_submit_grade.json"))) == 2


def test_canvas_connection_failure_is_reported_cleanly(monkeypatch) -> None:
    def explode(**_kwargs):
        raise RuntimeError("no route to host")

    monkeypatch.setattr(honor_cli, "create_canvas_client", explode)
    result = CliRunner().invoke(build_test_app(), ["honor-credit", "-c", "42", "-a", "1", "--json"])

    assert result.exit_code == 1
    assert "no route to host" in json.loads(result.output)["error"]
