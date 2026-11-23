"""CLI tests for ``coursewright.ui.cli.packet_tracer``."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import typer
from rich.console import Console
from typer.testing import CliRunner

from coursewright.core.canvas.models import (
    Assignment,
    Submission,
    SubmissionAttachment,
    SubmissionComment,
)
from coursewright.core.canvas.safe_write import default_backup_directory
from coursewright.core.config import AppConfig
from coursewright.ta.packettracer.classifier import COMPLETENESS_STATUS_MARKER
from coursewright.ui.cli import packet_tracer as pt_cli
from coursewright.ui.cli.packet_tracer import packet_tracer_command


def _build_cli_app() -> typer.Typer:
    """Build a minimal root Typer app mirroring ``main.py`` with ``ta packet-tracer``."""
    root = typer.Typer()
    ta = typer.Typer()
    ta.command("packet-tracer")(packet_tracer_command)
    root.add_typer(ta, name="ta")

    @root.callback()
    def _root(
        ctx: typer.Context,
        config_file: str = typer.Option("config.yaml", "--config", "-c"),
    ) -> None:
        ctx.obj = {
            "config_file": config_file,
            "config": AppConfig.load_from_file(config_file, create_if_missing=False),
        }

    return root


def _pt_assignment(
    assignment_id: int = 10,
    name: str = "11.2.1 Packet Tracer - NAT",
    points: float = 50.0,
) -> Assignment:
    return Assignment(
        id=assignment_id,
        name=name,
        allowed_extensions=["pka", "pdf"],
        points_possible=points,
        published=True,
    )


def _non_pt_assignment(assignment_id: int = 99, name: str = "Reading Essay") -> Assignment:
    return Assignment(id=assignment_id, name=name, published=True)


def _complete_submission(
    user_id: int = 1,
    name: str = "Student One",
    score: float | None = None,
) -> Submission:
    return Submission(
        id=user_id * 100,
        user_id=user_id,
        submitted_at="2025-01-15T12:00:00Z",
        workflow_state="submitted",
        score=score,
        attachments=[
            SubmissionAttachment(id=1, display_name="Report.pdf", filename="Report.pdf"),
            SubmissionAttachment(id=2, display_name="topo.pka", filename="topo.pka"),
        ],
        user={"name": name},
    )


def _incomplete_submission(
    user_id: int = 2,
    name: str = "Student Two",
) -> Submission:
    return Submission(
        id=user_id * 100,
        user_id=user_id,
        submitted_at="2025-01-15T12:00:00Z",
        workflow_state="submitted",
        attachments=[
            SubmissionAttachment(id=3, display_name="Report.pdf", filename="Report.pdf"),
        ],
        user={"name": name},
    )


def _populate_templates(workspace: Path) -> Path:
    templates_dir = workspace / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "missing_submission.md",
        "missing_lab_document.md",
        "missing_packet_tracer.md",
    ):
        (templates_dir / name).write_text(
            f"Please complete {{assignment_name}}, {{student_name}} ({name}).",
            encoding="utf-8",
        )
    return templates_dir


def test_help_displays_all_options() -> None:
    app = _build_cli_app()
    result = CliRunner().invoke(app, ["ta", "packet-tracer", "--help"])
    assert result.exit_code == 0, result.output
    for option in (
        "--course",
        "--assignment",
        "--auto-grade",
        "--post-feedback",
        "--allow-generic-fallback",
        "--execute",
        "--json",
    ):
        assert option in result.output


def test_scan_course_lists_detected_pt_assignments_and_json(monkeypatch) -> None:
    client = MagicMock()
    client.get_assignments.return_value = [
        _pt_assignment(assignment_id=10, name="11.2.1 Packet Tracer - NAT"),
        _non_pt_assignment(assignment_id=99, name="Reading Essay"),
    ]
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    app = _build_cli_app()
    rich_res = CliRunner().invoke(app, ["ta", "packet-tracer", "--course", "42"])
    assert rich_res.exit_code == 0, rich_res.output
    assert "11.2.1 Packet Tracer - NAT" in rich_res.output
    assert "Reading Essay" not in rich_res.output

    json_res = CliRunner().invoke(app, ["ta", "packet-tracer", "--course", "42", "--json"])
    assert json_res.exit_code == 0, json_res.output
    payload = json.loads(json_res.output)
    assert payload["course_id"] == 42
    assert payload["total_assignments"] == 2
    assert payload["detected_count"] == 1
    assert payload["assignments"][0]["id"] == 10


def test_check_assignment_outputs_table_and_clean_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    client = MagicMock()
    client.get_assignments.return_value = [_pt_assignment(assignment_id=10)]
    client.get_assignment_submissions.return_value = [
        _complete_submission(user_id=1, name="Student One"),
        _incomplete_submission(user_id=2, name="Student Two"),
    ]
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    terminal_console = Console(
        file=io.StringIO(),
        force_terminal=True,
        width=120,
        _environ={"TERM": "xterm-256color"},
    )
    monkeypatch.setattr(pt_cli, "console", terminal_console)

    app = _build_cli_app()
    json_res = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "missing.yaml"),
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "-a",
            "10",
            "--json",
        ],
    )
    assert json_res.exit_code == 0, json_res.output
    payload = json.loads(json_res.output)
    assert payload["summary"]["complete_count"] == 1
    assert payload["summary"]["incomplete_count"] == 1
    assert len(payload["reports"]) == 2
    # In JSON mode, Rich console stdout must stay completely unused.
    assert terminal_console.file.getvalue() == ""


def test_auto_grade_dry_run_works_without_workspace(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    client = MagicMock()
    client.get_assignments.return_value = [_pt_assignment(assignment_id=10, points=50.0)]
    client.get_assignment_submissions.return_value = [
        _complete_submission(user_id=1, name="Student One", score=None),
        _complete_submission(user_id=3, name="Student Three", score=50.0),
    ]
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    app = _build_cli_app()
    res = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "missing.yaml"),
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "-a",
            "10",
            "--auto-grade",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["execute"] is False
    assert payload["auto_grade"]["planned_count"] == 1
    assert payload["auto_grade"]["graded_count"] == 0
    assert payload["auto_grade"]["skipped_count"] == 1
    client.submit_grade.assert_not_called()


def test_execute_requires_workspace(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    client = MagicMock()
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    app = _build_cli_app()
    res = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "missing.yaml"),
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "-a",
            "10",
            "--auto-grade",
            "--execute",
        ],
    )
    assert res.exit_code == 1
    assert "workspace" in res.output.lower()
    client.submit_grade.assert_not_called()


def test_execute_auto_grade_writes_backups_and_reports_directory(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    workspace = tmp_path / "ws"
    config_file = tmp_path / "cfg.yaml"
    AppConfig(workspace=str(workspace)).save_to_file(config_file)

    client = MagicMock()
    client.get_assignments.return_value = [_pt_assignment(assignment_id=10, points=50.0)]
    client.get_assignment_submissions.return_value = [
        _complete_submission(user_id=1, name="Student One", score=None),
    ]
    client.get_submission.return_value = {"id": 100, "score": None}
    client.submit_grade.return_value = True
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    app = _build_cli_app()
    res = CliRunner().invoke(
        app,
        [
            "--config",
            str(config_file),
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "-a",
            "10",
            "--auto-grade",
            "--execute",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["execute"] is True
    assert payload["auto_grade"]["graded_count"] == 1
    expected_backup_dir = default_backup_directory(workspace)
    assert payload["backup_directory"] == str(expected_backup_dir)
    assert len(list(expected_backup_dir.glob("*_submit_grade.json"))) == 1


def test_post_feedback_missing_templates_fails_in_dry_run_unless_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    client = MagicMock()
    client.get_assignments.return_value = [_pt_assignment(assignment_id=10)]
    client.get_assignment_submissions.return_value = [
        _incomplete_submission(user_id=2, name="Student Two"),
    ]
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    app = _build_cli_app()
    fail_res = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "missing.yaml"),
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "-a",
            "10",
            "--post-feedback",
        ],
    )
    assert fail_res.exit_code == 1
    assert "templates" in fail_res.output.lower()
    assert "--allow-generic-fallback" in fail_res.output
    client.post_submission_comment.assert_not_called()

    ok_res = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "missing.yaml"),
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "-a",
            "10",
            "--post-feedback",
            "--allow-generic-fallback",
            "--json",
        ],
    )
    assert ok_res.exit_code == 0, ok_res.output
    payload = json.loads(ok_res.output)
    assert payload["post_feedback"]["used_generic_fallback"] is True
    assert payload["post_feedback"]["planned_count"] == 2
    client.post_submission_comment.assert_not_called()


def test_post_feedback_execute_with_templates_writes_backups(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    _populate_templates(tmp_path)

    client = MagicMock()
    client.get_assignments.return_value = [_pt_assignment(assignment_id=10)]
    client.get_assignment_submissions.return_value = [
        _incomplete_submission(user_id=2, name="Student Two"),
    ]
    client.get_submissions_with_comments.return_value = [
        Submission(
            id=200,
            user_id=2,
            submission_comments=[
                SubmissionComment(id=55, comment=f"{COMPLETENESS_STATUS_MARKER} old"),
            ],
        )
    ]
    client.get_submission.return_value = {"id": 200, "score": None}
    client.delete_submission_comment.return_value = True
    client.post_submission_comment.return_value = True
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    app = _build_cli_app()
    res = CliRunner().invoke(
        app,
        [
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "-a",
            "10",
            "--post-feedback",
            "--execute",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Backups saved to:" in res.output
    client.delete_submission_comment.assert_called_once_with(42, 10, 2, 55)
    assert client.post_submission_comment.call_count == 2
    assert len(list(default_backup_directory(tmp_path).glob("*_post_submission_comment.json"))) == 2


def test_action_flags_without_assignment_fail_cleanly(monkeypatch, tmp_path: Path) -> None:
    client = MagicMock()
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    app = _build_cli_app()
    res = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "missing.yaml"),
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "--auto-grade",
            "--json",
        ],
    )
    assert res.exit_code == 1
    payload = json.loads(res.output)
    assert "assignment" in payload["error"].lower()


def test_canvas_auth_failure_exits_cleanly_in_json_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: None)

    app = _build_cli_app()
    res = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "missing.yaml"),
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "--json",
        ],
    )
    assert res.exit_code == 1
    payload = json.loads(res.output)
    assert "error" in payload


def test_combined_auto_grade_and_post_feedback_aborts_before_writes_if_templates_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    client = MagicMock()
    client.get_assignments.return_value = [_pt_assignment(assignment_id=10, points=50.0)]
    client.get_assignment_submissions.return_value = [
        _complete_submission(user_id=1, name="Student One", score=None),
        _incomplete_submission(user_id=2, name="Student Two"),
    ]
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    app = _build_cli_app()
    res = CliRunner().invoke(
        app,
        [
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "-a",
            "10",
            "--auto-grade",
            "--post-feedback",
            "--execute",
            "--json",
        ],
    )
    assert res.exit_code == 1
    payload = json.loads(res.output)
    assert "templates" in payload["error"].lower()
    client.submit_grade.assert_not_called()
    client.post_submission_comment.assert_not_called()


def test_check_assignment_rich_table_renders_students(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    client = MagicMock()
    client.get_assignments.return_value = [_pt_assignment(assignment_id=10)]
    client.get_assignment_submissions.return_value = [
        _complete_submission(user_id=1, name="Student One"),
        _incomplete_submission(user_id=2, name="Student Two"),
    ]
    monkeypatch.setattr(pt_cli, "create_canvas_client", lambda **_kwargs: client)

    app = _build_cli_app()
    res = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "missing.yaml"),
            "ta",
            "packet-tracer",
            "-c",
            "42",
            "-a",
            "10",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Student One" in res.output
    assert "Student Two" in res.output
    assert "Completeness Summary" in res.output
