"""CLI tests for ``coursewright ta zybooks-import`` wrapper."""

from __future__ import annotations

import csv
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import typer
from typer.testing import CliRunner

from coursewright.core.config import AppConfig
from coursewright.core.inbox import CandidateLocation, InboxCandidate
from coursewright.ta.zybooks.importer import MAPPING_CACHE_FOLDER_NAME
from coursewright.ui.cli import zybooks_import as zybooks_import_cli
from coursewright.ui.cli.zybooks_import import zybooks_import_command


def build_test_app(config: AppConfig | None = None) -> typer.Typer:
    root = typer.Typer()
    ta = typer.Typer()
    root.add_typer(ta, name="ta")
    ta.command("zybooks-import")(zybooks_import_command)

    @root.callback()
    def _setup(ctx: typer.Context) -> None:
        ctx.obj = {"config": config or AppConfig()}

    return root


def write_unit_csv(
    path: Path,
    *,
    chapter: int = 2,
    labs: tuple[int, ...] = (100, 90, 80, 70, 60, 50),
    pa: int = 80,
    ca: int = 60,
    extra_unmatched_student: bool = True,
) -> Path:
    sections = [f"{chapter}.1"] + [f"{chapter}.{10 + i}" for i in range(len(labs))]
    columns = []
    for category in ("Participation", "Challenge", "Lab"):
        for sec in sections:
            maximum, score = 0, "N/A"
            if sec == f"{chapter}.1" and category in {"Participation", "Challenge"}:
                maximum, score = (10, pa) if category == "Participation" else (5, ca)
            elif sec != f"{chapter}.1" and category == "Lab":
                idx = int(sec.split(".")[1]) - 10
                maximum, score = 10, labs[idx]
            columns.append((f"{sec} - {category} ({maximum})", score))
    lab_max = 10 * len(labs)
    headers = [
        "Last name",
        "First name",
        "Primary email",
        "Class section",
        f"Total ({15 + lab_max})",
        "Participation total (10)",
        "Challenge total (5)",
        f"Lab total ({lab_max})",
    ]
    rows = [
        ["Example", "Student", "example@example.invalid", "", 70, pa, ca, 75]
        + [score for _, score in columns]
    ]
    if extra_unmatched_student:
        rows.append(
            ["Unmatched", "Learner", "unmatched@example.invalid", "", 50, 50, 50, 50]
            + [50 if score != "N/A" else "N/A" for _, score in columns]
        )
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers + [name for name, _ in columns])
        writer.writerows(rows)
    return path


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeCanvasClient:
    def __init__(
        self,
        *,
        assignments=None,
        modules=None,
        pages=None,
        initial_scores=None,
        extra_canvas_student: bool = True,
    ):
        self.base_url = "https://canvas.example.edu"
        self.roster = [
            {"id": 1, "name": "Student Example", "email": "example@example.invalid"},
        ]
        if extra_canvas_student:
            self.roster.append(
                {"id": 2, "name": "Extra Canvas", "email": "extra@example.invalid"}
            )
        self.assignments = assignments or [
            {
                "id": 101,
                "name": "U01A01: zyBook Participation & Challenge",
                "description": "<p>Chapter 2</p>",
                "points_possible": 50,
            },
            {
                "id": 102,
                "name": "U01A02: zyLabs",
                "description": "<p>Chapter 2</p>",
                "points_possible": 50,
            },
        ]
        scores = initial_scores or {}
        self.records = []
        for assignment in self.assignments:
            for user in self.roster:
                aid = assignment["id"]
                uid = user["id"]
                self.records.append(
                    {
                        "assignment_id": aid,
                        "user_id": uid,
                        "score": scores.get((aid, uid)),
                        "excused": False,
                        "workflow_state": "graded" if (aid, uid) in scores else "unsubmitted",
                    }
                )
        self.modules = modules if modules is not None else []
        self.pages = pages if pages is not None else {}
        self.writes = []

    def get_course_users(self, course_id):
        return deepcopy(self.roster)

    def get_assignments(self, course_id):
        return deepcopy(self.assignments)

    def get_all_student_submissions(self, course_id):
        return deepcopy(self.records)

    def get_student_submissions(self, course_id, user_id):
        return [deepcopy(row) for row in self.records if row["user_id"] == user_id]

    def get_submission(self, course_id, assignment_id, user_id):
        return next(
            deepcopy(row)
            for row in self.records
            if row["assignment_id"] == assignment_id and row["user_id"] == user_id
        )

    def submit_grade(self, course_id, assignment_id, user_id, score, comment=None):
        self.writes.append((course_id, assignment_id, user_id, score))
        row = next(
            row
            for row in self.records
            if row["assignment_id"] == assignment_id and row["user_id"] == user_id
        )
        row.update(score=score, workflow_state="graded", grade=str(score))
        return True

    def _make_request(self, method, url, **kwargs):
        if url.endswith("/modules"):
            return FakeResponse(self.modules)
        for slug, body in self.pages.items():
            if url.endswith(f"/pages/{slug}"):
                return FakeResponse({"title": slug, "body": body})
        return FakeResponse({}, status_code=404)


def test_dry_run_default_never_writes_canvas_or_cache_and_shows_rich_table(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    csv_path = write_unit_csv(tmp_path / "ExampleBook_report_1.csv")
    canvas = FakeCanvasClient()
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)

    app = build_test_app(AppConfig(workspace=str(workspace)))
    result = CliRunner().invoke(
        app,
        [
            "ta",
            "zybooks-import",
            "--course",
            "42",
            "--file",
            str(csv_path),
            "--confirm-distinct-labs",
        ],
    )

    assert result.exit_code == 0, result.output
    for heading in ("Student", "Assignment", "Old", "New", "Status", "Risky"):
        assert heading in result.output
    assert "Student Example" in result.output
    assert "Unmatched source students: 1" in result.output
    assert "Unmatched Canvas students: 1" in result.output
    assert "Dry run" in result.output
    assert canvas.writes == []
    assert not (workspace / MAPPING_CACHE_FOLDER_NAME).exists()
    assert not (workspace / "backups").exists()


def test_dry_run_json_mode_works_without_workspace_and_emits_pure_json(tmp_path, monkeypatch):
    csv_path = write_unit_csv(tmp_path / "ExampleBook_report_1.csv")
    canvas = FakeCanvasClient()
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)

    app = build_test_app(AppConfig())
    result = CliRunner().invoke(
        app,
        [
            "ta",
            "zybooks-import",
            "--course",
            "42",
            "--file",
            str(csv_path),
            "--confirm-distinct-labs",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["course_id"] == 42
    assert payload["book_id"] == "ExampleBook"
    assert payload["dry_run"] is True
    assert payload["execute"] is False
    assert payload["unmatched_source"] == 1
    assert payload["unmatched_canvas"] == 1
    assert payload["counts"]["ready"] == 2
    assert len(payload["rows"]) == 2
    assert canvas.writes == []


def test_auto_discovery_and_missing_csv_failure(tmp_path, monkeypatch):
    csv_path = write_unit_csv(tmp_path / "DiscoveredBook_report_2.csv")
    canvas = FakeCanvasClient()
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)
    monkeypatch.setattr(
        zybooks_import_cli,
        "find_score_report_candidate",
        lambda **_kw: InboxCandidate(
            path=csv_path,
            found_in=CandidateLocation.DOWNLOADS,
            modified_at=datetime.now(UTC),
        ),
    )

    app = build_test_app(AppConfig())
    result = CliRunner().invoke(
        app,
        ["ta", "zybooks-import", "-c", "42", "--confirm-distinct-labs", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["book_id"] == "DiscoveredBook"
    assert payload["csv_path"] == str(csv_path.resolve())

    monkeypatch.setattr(zybooks_import_cli, "find_score_report_candidate", lambda **_kw: None)
    missing_result = CliRunner().invoke(app, ["ta", "zybooks-import", "-c", "42", "--json"])
    assert missing_result.exit_code == 1
    assert "error" in json.loads(missing_result.output)


def test_cli_falls_back_to_module_section_hints_when_assignments_lack_sections(
    tmp_path, monkeypatch
):
    csv_path = write_unit_csv(tmp_path / "HintBook_report_1.csv", chapter=11)
    canvas = FakeCanvasClient(
        assignments=[
            {
                "id": 201,
                "name": "U04A01: zyBook Participation & Challenge",
                "description": "",
                "points_possible": 50,
            },
            {
                "id": 202,
                "name": "U04A02: zyLabs",
                "description": "<p>Complete 5 labs.</p>",
                "points_possible": 50,
            },
        ],
        modules=[
            {
                "id": 10,
                "name": "Unit 4",
                "items": [
                    {"type": "Page", "page_url": "unit-4-overview"},
                    {"type": "Assignment", "content_id": 201},
                    {"type": "Assignment", "content_id": 202},
                ],
            }
        ],
        pages={"unit-4-overview": "<p>Read Section 11.1 before starting labs.</p>"},
    )
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)

    app = build_test_app(AppConfig())
    result = CliRunner().invoke(
        app,
        [
            "ta",
            "zybooks-import",
            "-c",
            "42",
            "-f",
            str(csv_path),
            "--confirm-distinct-labs",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["used_module_hints"] is True
    assert len(payload["units"]) == 1
    assert payload["units"][0]["name"] == "U04"
    assert len(payload["units"][0]["lab_sections"]) == 6
    assert payload["counts"]["ready"] == 2


def test_confirm_distinct_labs_is_enforced_before_preview_or_write(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    csv_path = write_unit_csv(tmp_path / "ExampleBook_report_1.csv")
    canvas = FakeCanvasClient()
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)

    app = build_test_app(AppConfig(workspace=str(workspace)))
    result = CliRunner().invoke(
        app,
        ["ta", "zybooks-import", "-c", "42", "-f", str(csv_path)],
    )

    assert result.exit_code == 1
    assert "--confirm-distinct-labs" in result.output
    assert canvas.writes == []
    assert not (workspace / MAPPING_CACHE_FOLDER_NAME).exists()


def test_execute_persists_cache_writes_backups_and_reports_verification(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    csv_path = write_unit_csv(tmp_path / "ExampleBook_report_1.csv")
    canvas = FakeCanvasClient()
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)

    app = build_test_app(AppConfig(workspace=str(workspace)))
    result = CliRunner().invoke(
        app,
        [
            "ta",
            "zybooks-import",
            "-c",
            "42",
            "-f",
            str(csv_path),
            "--confirm-distinct-labs",
            "--execute",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(canvas.writes) == 2
    assert (42, 101, 1, 35.0) in canvas.writes
    assert (42, 102, 1, 40.0) in canvas.writes
    assert list((workspace / MAPPING_CACHE_FOLDER_NAME).glob("42-*.json"))
    backup_dir = workspace / "backups" / "canvas"
    assert len(list(backup_dir.glob("*_submit_grade.json"))) == 2
    assert "Verified" in result.output
    assert str(backup_dir) in result.output


def test_execute_requires_workspace_and_respects_approve_zeros_and_include_existing(
    tmp_path, monkeypatch
):
    csv_path = write_unit_csv(
        tmp_path / "ExampleBook_report_1.csv",
        labs=(0, 0, 0, 0, 0, 0),
        pa=0,
        ca=0,
    )
    canvas = FakeCanvasClient(initial_scores={(101, 1): 40.0})
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)

    # Without workspace, --execute refuses before writing.
    no_ws_app = build_test_app(AppConfig())
    no_ws_result = CliRunner().invoke(
        no_ws_app,
        [
            "ta",
            "zybooks-import",
            "-c",
            "42",
            "-f",
            str(csv_path),
            "--confirm-distinct-labs",
            "--execute",
        ],
    )
    assert no_ws_result.exit_code == 1
    assert "workspace" in no_ws_result.output.lower()
    assert canvas.writes == []

    # With workspace but without --approve-zeros, risky 0.00 lab write is blocked.
    workspace = tmp_path / "workspace"
    ws_app = build_test_app(AppConfig(workspace=str(workspace)))
    blocked_result = CliRunner().invoke(
        ws_app,
        [
            "ta",
            "zybooks-import",
            "-c",
            "42",
            "-f",
            str(csv_path),
            "--confirm-distinct-labs",
            "--execute",
        ],
    )
    assert blocked_result.exit_code == 1
    assert "--approve-zeros" in blocked_result.output
    assert canvas.writes == []

    # With --approve-zeros (without --include-existing), lab 0 writes and higher 101 stays skipped.
    approved_result = CliRunner().invoke(
        ws_app,
        [
            "ta",
            "zybooks-import",
            "-c",
            "42",
            "-f",
            str(csv_path),
            "--confirm-distinct-labs",
            "--approve-zeros",
            "--execute",
            "--json",
        ],
    )
    assert approved_result.exit_code == 0, approved_result.output
    payload = json.loads(approved_result.output)
    assert payload["verification"]["verified"] == 1
    assert canvas.writes == [(42, 102, 1, 0.0)]

    # With --include-existing and --approve-zeros, assignment 101 lowers from 40 to 0.
    lower_result = CliRunner().invoke(
        ws_app,
        [
            "ta",
            "zybooks-import",
            "-c",
            "42",
            "-f",
            str(csv_path),
            "--confirm-distinct-labs",
            "--include-existing",
            "--approve-zeros",
            "--execute",
            "--json",
        ],
    )
    assert lower_result.exit_code == 0, lower_result.output
    lower_payload = json.loads(lower_result.output)
    assert lower_payload["verification"]["verified"] == 1
    assert (42, 101, 1, 0.0) in canvas.writes


def test_execute_when_no_ready_rows_reports_nothing_to_send(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    csv_path = write_unit_csv(tmp_path / "ExampleBook_report_1.csv")
    canvas = FakeCanvasClient(initial_scores={(101, 1): 35.0, (102, 1): 40.0})
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)

    app = build_test_app(AppConfig(workspace=str(workspace)))
    result = CliRunner().invoke(
        app,
        [
            "ta",
            "zybooks-import",
            "-c",
            "42",
            "-f",
            str(csv_path),
            "--confirm-distinct-labs",
            "--execute",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Nothing to send" in result.output
    assert canvas.writes == []


def test_error_paths_emit_clean_failures_and_json_errors(tmp_path, monkeypatch):
    csv_path = write_unit_csv(tmp_path / "no_book_marker.csv")
    app = build_test_app(AppConfig())

    # Filename without _report_ and no --book-id fails loudly.
    missing_book = CliRunner().invoke(
        app, ["ta", "zybooks-import", "-c", "42", "-f", str(csv_path), "--json"]
    )
    assert missing_book.exit_code == 1
    assert "error" in json.loads(missing_book.output)

    # Canvas connection failure fails cleanly.
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: None)
    conn_fail = CliRunner().invoke(
        app,
        ["ta", "zybooks-import", "-c", "42", "-f", str(csv_path), "--book-id", "B1", "--json"],
    )
    assert conn_fail.exit_code == 1
    assert "error" in json.loads(conn_fail.output)

    # No mappable assignments fails cleanly.
    canvas = FakeCanvasClient(
        assignments=[{"id": 999, "name": "Midterm Exam", "description": "", "points_possible": 50}]
    )
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)
    no_targets = CliRunner().invoke(
        app,
        ["ta", "zybooks-import", "-c", "42", "-f", str(csv_path), "--book-id", "B1"],
    )
    assert no_targets.exit_code == 1
    assert "No mapped" in no_targets.output

    # Verify create_canvas_client receives configured URL and token file.
    mock_connect = Mock(return_value=None)
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", mock_connect)
    cfg = AppConfig()
    cfg.discussion.canvas_url = "https://custom.example.edu"
    cfg.discussion.api_token_file = "/tmp/custom_token.txt"
    CliRunner().invoke(
        build_test_app(cfg),
        ["ta", "zybooks-import", "-c", "42", "-f", str(csv_path), "--book-id", "B1"],
    )
    mock_connect.assert_called_once_with(
        base_url="https://custom.example.edu",
        api_token_file="/tmp/custom_token.txt",
    )


def test_cached_mappings_reuse_and_workspace_import_discovery(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    import_dir = workspace / "import"
    import_dir.mkdir(parents=True)
    csv_path = write_unit_csv(import_dir / "WorkspaceBook_report_1.csv")
    empty_downloads = tmp_path / "downloads"
    empty_downloads.mkdir()
    monkeypatch.setattr(
        "coursewright.core.inbox.discovery.default_download_directory",
        lambda: empty_downloads,
    )
    canvas = FakeCanvasClient()
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)

    app = build_test_app(AppConfig(workspace=str(workspace)))
    first_exec = CliRunner().invoke(
        app,
        ["ta", "zybooks-import", "-c", "42", "--confirm-distinct-labs", "--execute", "--json"],
    )
    assert first_exec.exit_code == 0, first_exec.output
    first_payload = json.loads(first_exec.output)
    assert first_payload["csv_path"] == str(csv_path.resolve())
    assert first_payload["cached_mappings"] is False

    # Second dry-run reuses the approved cached mappings even without --confirm-distinct-labs.
    second_dry = CliRunner().invoke(
        app,
        ["ta", "zybooks-import", "-c", "42", "--json"],
    )
    assert second_dry.exit_code == 0, second_dry.output
    second_payload = json.loads(second_dry.output)
    assert second_payload["cached_mappings"] is True
    assert second_payload["dry_run"] is True
    assert second_payload["counts"]["skipped"] == 2



def run_lab_preview(tmp_path, monkeypatch, *extra_arguments):
    csv_path = write_unit_csv(tmp_path / "ExampleBook_report_1.csv")
    canvas = FakeCanvasClient()
    monkeypatch.setattr(zybooks_import_cli, "create_canvas_client", lambda **_kw: canvas)
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    result = CliRunner().invoke(
        build_test_app(AppConfig()),
        [
            "ta",
            "zybooks-import",
            "--course",
            "42",
            "--file",
            str(csv_path),
            "--confirm-distinct-labs",
            "--json",
            *extra_arguments,
        ],
    )
    assert canvas.writes == []
    return result


def test_labs_required_defaults_to_best_five_and_can_be_raised(tmp_path, monkeypatch):
    # Six labs scored 100..50 on a 50-point target.
    default = run_lab_preview(tmp_path, monkeypatch)
    assert default.exit_code == 0, default.output
    payload = json.loads(default.output)
    assert payload["units"][0]["labs_required"] == 5
    lab_row = next(row for row in payload["rows"] if row["assignment_id"] == 102)
    assert lab_row["new_points"] == "40.00"  # best five average 80%

    all_six = run_lab_preview(tmp_path, monkeypatch, "--labs-required", "6")
    assert all_six.exit_code == 0, all_six.output
    payload = json.loads(all_six.output)
    assert payload["units"][0]["labs_required"] == 6
    lab_row = next(row for row in payload["rows"] if row["assignment_id"] == 102)
    assert lab_row["new_points"] == "37.50"  # all six average 75%


def test_labs_required_larger_than_the_lab_pool_blocks_the_preview(tmp_path, monkeypatch):
    result = run_lab_preview(tmp_path, monkeypatch, "--labs-required", "7")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert "at least 7 distinct" in payload["error"]
    assert "--labs-required" in payload["error"]
