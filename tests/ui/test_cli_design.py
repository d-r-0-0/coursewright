"""Tests for the ``coursewright design`` command groups.

Every command gets three kinds of test, all against a fake client (no
network): a ``--help`` test, a dry-run test and an execute test.  The
dry-run tests are the proof of card acceptance 3: without ``--execute``
the fake records **no** write and no backup file appears.  A last group
shows the remote activity display runs around every Canvas command.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from coursewright.core import traffic
from coursewright.ui.cli import design as design_cli
from coursewright.ui.cli.main import app

COURSE_ID = 101

SCHEDULE_TEXT = "Unit\tAssigned\t1st Post\tDue\nUnit 01\t09/02/2030\t09/06/2030\t09/13/2030\n"


class FakeCanvas:
    """A small in-memory Canvas.  Every write is recorded in ``writes``."""

    base_url = "https://canvas.example.edu"

    def __init__(self) -> None:
        self.writes: list[tuple[str, tuple[Any, ...]]] = []
        self.modules = [
            {"id": 1, "name": "Unit 01 Intro", "published": False, "unlock_at": None},
            {"id": 2, "name": "Unit 02 Next", "published": True, "unlock_at": None},
        ]
        self.items = {
            1: [
                {
                    "id": 11,
                    "title": "U01A01 Task",
                    "position": 1,
                    "type": "Assignment",
                    "content_id": 501,
                    "published": False,
                },
                {
                    "id": 12,
                    "title": "U01R01 Read",
                    "position": 2,
                    "type": "Page",
                    "content_id": None,
                    "published": False,
                },
            ],
            2: [],
        }

    # -- traffic: pretend each call is a slow round trip ----------------

    def _trip(self, target: str) -> None:
        traffic.publish_http_round_trip("canvas", "GET", target, 0, 0.0)
        time.sleep(0.005)
        traffic.publish_http_round_trip("canvas", "GET", target, 200, 5.0)

    # -- reads ----------------------------------------------------------

    def get_course(self, course_id: int) -> dict[str, Any]:
        self._trip(f"courses/{course_id}")
        return {"id": course_id, "name": "Example Course", "workflow_state": "available"}

    def get_modules(self, course_id: int) -> list[dict[str, Any]]:
        self._trip(f"courses/{course_id}/modules")
        return self.modules

    def get_module(self, course_id: int, module_id: int) -> dict[str, Any]:
        return next(m for m in self.modules if m["id"] == module_id)

    def get_module_items(self, course_id: int, module_id: int) -> list[dict[str, Any]]:
        self._trip(f"courses/{course_id}/modules/{module_id}/items")
        return self.items[module_id]

    def get_module_item(self, course_id: int, module_id: int, item_id: int) -> dict[str, Any]:
        return next(i for i in self.items[module_id] if i["id"] == item_id)

    def get_assignment(self, course_id: int, assignment_id: int) -> dict[str, Any]:
        return {"id": assignment_id}

    def get_discussion_topics(self, course_id: int) -> list[dict[str, Any]]:
        return []

    def get_assignments(self, course_id: int) -> list[Any]:
        return []

    def _paginated_get(self, url: str, params: dict[str, Any] | None = None) -> list[Any]:
        return []

    def get_assignment_groups(self, course_id: int) -> list[Any]:
        return []

    get_quizzes = get_announcements = get_pages = get_assignment_groups

    def get_page(self, course_id: int, page_url: str) -> None:
        return None

    def _make_request(self, method: str, url: str, params: Any = None) -> SimpleNamespace:
        """Serve the strict paginated reads that publishing uses."""
        self._trip("courses/modules")
        if url.endswith("/modules"):
            body: list[Any] = self.modules
        else:
            module_id = int(url.rsplit("/modules/", 1)[1].split("/")[0])
            body = self.items[module_id]
        return SimpleNamespace(status_code=200, json=lambda: body, links={})

    # -- writes ---------------------------------------------------------

    def _record(self, name: str, *args: Any) -> dict[str, Any]:
        self.writes.append((name, args))
        return {"id": 900 + len(self.writes)}

    def update_module_item(self, *args: Any) -> dict[str, Any]:
        return self._record("update_module_item", *args)

    def update_module(self, *args: Any) -> dict[str, Any]:
        return self._record("update_module", *args)

    def update_assignment(self, *args: Any) -> dict[str, Any]:
        return self._record("update_assignment", *args)

    def update_quiz(self, *args: Any) -> dict[str, Any]:
        return self._record("update_quiz", *args)

    def create_discussion_topic(self, *args: Any) -> dict[str, Any]:
        return self._record("create_discussion_topic", *args)

    def create_module_item(self, *args: Any) -> dict[str, Any]:
        return self._record("create_module_item", *args)


@pytest.fixture(autouse=True)
def clean_traffic_bus():
    traffic.reset_for_tests()
    yield
    traffic.reset_for_tests()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace_directory = tmp_path / "workspace"
    workspace_directory.mkdir()
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(workspace_directory))
    return workspace_directory


@pytest.fixture
def fake_canvas(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> FakeCanvas:
    fake = FakeCanvas()
    monkeypatch.setattr(design_cli, "get_canvas_client", lambda _config: fake)
    return fake


@pytest.fixture
def schedule_file(tmp_path: Path) -> Path:
    path = tmp_path / "schedule.tsv"
    path.write_text(SCHEDULE_TEXT, encoding="utf-8")
    return path


def run(*arguments: str):
    return CliRunner().invoke(app, ["design", *arguments])


def backup_files(workspace: Path) -> list[Path]:
    return [path for path in workspace.rglob("*.json")]


# The write commands, each as (name, arguments without --execute).
def write_commands(schedule_file: Path, tmp_path: Path) -> dict[str, list[str]]:
    return {
        "organize": ["organize", "unit-order", str(COURSE_ID)],
        "discussions": ["discussions", "create", str(COURSE_ID)],
        "dates": ["dates", "apply", str(COURSE_ID), str(schedule_file)],
        "publish": ["publish", "content", str(COURSE_ID)],
    }


# -- --help ----------------------------------------------------------------

HELP_ARGUMENTS = [
    ["diagnose"],
    ["diagnose", "run"],
    ["organize"],
    ["organize", "unit-order"],
    ["discussions"],
    ["discussions", "create"],
    ["dates"],
    ["dates", "apply"],
    ["publish"],
    ["publish", "content"],
    ["audit"],
    ["convert"],
    ["convert", "names"],
    ["convert", "captions"],
]


@pytest.mark.parametrize("arguments", HELP_ARGUMENTS, ids=" ".join)
def test_command_help(arguments: list[str]) -> None:
    result = run(*arguments, "--help")
    assert result.exit_code == 0, result.output
    assert "Usage" in result.output


@pytest.mark.parametrize(
    "arguments",
    [
        ["organize", "unit-order"],
        ["discussions", "create"],
        ["dates", "apply"],
        ["publish", "content"],
        ["convert", "names"],
        ["convert", "captions"],
    ],
    ids=" ".join,
)
def test_write_command_help_offers_execute(arguments: list[str]) -> None:
    assert "--execute" in run(*arguments, "--help").output


def test_design_help_lists_every_group() -> None:
    output = run("--help").output
    for name in ("diagnose", "organize", "discussions", "dates", "publish", "audit", "convert"):
        assert name in output


# -- read-only commands ----------------------------------------------------


def test_diagnose_shows_a_table_and_writes_nothing(fake_canvas: FakeCanvas) -> None:
    result = run("diagnose", "run", str(COURSE_ID))
    assert result.exit_code == 0, result.output
    assert "Example Course" in result.output
    assert "Unpublished modules" in result.output
    assert fake_canvas.writes == []


def test_audit_shows_a_status_per_standard(fake_canvas: FakeCanvas) -> None:
    result = run("audit", str(COURSE_ID))
    assert result.exit_code == 0, result.output
    assert "Audit against oscqr" in result.output
    assert fake_canvas.writes == []


def test_audit_unknown_rubric_is_a_clean_error(fake_canvas: FakeCanvas) -> None:
    result = run("audit", str(COURSE_ID), "--rubric", "no-such-rubric")
    assert result.exit_code == 1
    assert "Audit failed" in result.output


# -- dry run: the preview table, and not one write -------------------------


def test_unit_order_dry_run_previews_moves(fake_canvas: FakeCanvas, workspace: Path) -> None:
    result = run("organize", "unit-order", str(COURSE_ID))
    assert result.exit_code == 0, result.output
    assert "Module item moves" in result.output
    assert "U01R01 Read" in result.output
    assert "Preview (dry run)" in result.output
    assert fake_canvas.writes == []
    assert backup_files(workspace) == []


def test_discussions_dry_run_previews_topics(fake_canvas: FakeCanvas, workspace: Path) -> None:
    result = run("discussions", "create", str(COURSE_ID))
    assert result.exit_code == 0, result.output
    assert "Discussion topics" in result.output
    assert "Getting to Know You" in result.output
    assert "Preview (dry run)" in result.output
    assert fake_canvas.writes == []
    assert backup_files(workspace) == []


def test_dates_dry_run_previews_changes(
    fake_canvas: FakeCanvas, workspace: Path, schedule_file: Path
) -> None:
    result = run("dates", "apply", str(COURSE_ID), str(schedule_file))
    assert result.exit_code == 0, result.output
    assert "Date changes" in result.output
    assert "2030-09-13T12:00:00" in result.output  # Canvas date: no Z suffix
    assert "Preview (dry run)" in result.output
    assert fake_canvas.writes == []
    assert backup_files(workspace) == []


def test_publish_dry_run_previews_plan(fake_canvas: FakeCanvas, workspace: Path) -> None:
    result = run("publish", "content", str(COURSE_ID))
    assert result.exit_code == 0, result.output
    assert "Publishing plan" in result.output
    assert "Unit 01 Intro" in result.output
    # Every unpublished item is listed by name, not just counted.
    assert "U01A01 Task" in result.output
    assert "U01R01 Read" in result.output
    assert "Preview (dry run)" in result.output
    assert fake_canvas.writes == []
    assert backup_files(workspace) == []


def test_publish_first_limits_the_plan(fake_canvas: FakeCanvas) -> None:
    result = run("publish", "content", str(COURSE_ID), "--first", "1")
    assert result.exit_code == 0, result.output
    assert "Unit 02 Next" not in result.output


def test_publish_first_out_of_range_is_a_clean_error(fake_canvas: FakeCanvas) -> None:
    result = run("publish", "content", str(COURSE_ID), "--first", "9")
    assert result.exit_code == 1
    assert "Bad --first" in result.output


def test_convert_names_dry_run_renames_nothing(tmp_path: Path) -> None:
    target = tmp_path / "Lecture Notes.txt"
    target.write_text("x", encoding="utf-8")
    result = run("convert", "names", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert target.exists()
    assert "Preview (dry run)" in result.output


def test_convert_captions_dry_run_writes_nothing(tmp_path: Path) -> None:
    captions = tmp_path / "panopto_captions"
    captions.mkdir()
    (captions / "Intro_abc.txt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHello.\n")
    result = run("convert", "captions", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Preview (dry run)" in result.output
    assert not (tmp_path / "transcripts").exists()


# -- execute: the writes happen, backup first ------------------------------


def test_unit_order_execute_writes_with_backup(fake_canvas: FakeCanvas, workspace: Path) -> None:
    result = run("organize", "unit-order", str(COURSE_ID), "--execute")
    assert result.exit_code == 0, result.output
    assert "Done." in result.output
    assert [name for name, _ in fake_canvas.writes] == ["update_module_item"] * 2
    assert len(backup_files(workspace)) == 2


def test_discussions_execute_creates_topics(fake_canvas: FakeCanvas, workspace: Path) -> None:
    result = run("discussions", "create", str(COURSE_ID), "--execute")
    assert result.exit_code == 0, result.output
    assert "Done." in result.output
    names = [name for name, _ in fake_canvas.writes]
    assert names.count("create_discussion_topic") == 1  # Unit 01 only: odd units
    assert names.count("create_module_item") == 1


def test_dates_execute_applies_schedule(
    fake_canvas: FakeCanvas, workspace: Path, schedule_file: Path
) -> None:
    result = run("dates", "apply", str(COURSE_ID), str(schedule_file), "--execute")
    assert result.exit_code == 0, result.output
    assert "Done." in result.output
    names = [name for name, _ in fake_canvas.writes]
    assert "update_module" in names
    assert "update_assignment" in names
    assert backup_files(workspace)


def test_dates_missing_schedule_is_a_clean_error(fake_canvas: FakeCanvas, tmp_path: Path) -> None:
    result = run("dates", "apply", str(COURSE_ID), str(tmp_path / "missing.tsv"))
    assert result.exit_code == 1
    assert "Schedule problem" in result.output


def test_publish_execute_publishes_with_backup(fake_canvas: FakeCanvas, workspace: Path) -> None:
    result = run("publish", "content", str(COURSE_ID), "--execute")
    assert result.exit_code == 0, result.output
    assert "Done." in result.output
    assert ("update_module", (COURSE_ID, 1, {"published": True})) in fake_canvas.writes
    assert backup_files(workspace)


def test_convert_names_execute_renames(tmp_path: Path) -> None:
    (tmp_path / "Lecture Notes.txt").write_text("x", encoding="utf-8")
    result = run("convert", "names", str(tmp_path), "--execute")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "lecture-notes.txt").exists()
    assert "Done." in result.output


def test_convert_captions_execute_writes_transcripts(tmp_path: Path) -> None:
    captions = tmp_path / "panopto_captions"
    captions.mkdir()
    (captions / "Intro_abc.txt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n", encoding="utf-8"
    )
    result = run("convert", "captions", str(tmp_path), "--execute")
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "transcripts").glob("*"))


# -- acceptance 3: nothing writes without --execute ------------------------


@pytest.mark.parametrize("group", ["organize", "discussions", "dates", "publish"])
def test_no_write_without_execute(
    group: str, fake_canvas: FakeCanvas, workspace: Path, schedule_file: Path, tmp_path: Path
) -> None:
    arguments = write_commands(schedule_file, tmp_path)[group]
    result = run(*arguments)
    assert result.exit_code == 0, result.output
    assert fake_canvas.writes == []
    assert backup_files(workspace) == []


def test_commands_refuse_to_run_without_a_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Even a preview needs a backup folder, so it never over-promises."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.setattr(design_cli, "get_canvas_client", lambda _config: FakeCanvas())
    monkeypatch.setattr(design_cli, "resolve_workspace_directory", lambda _config: None)
    result = run("organize", "unit-order", str(COURSE_ID))
    assert result.exit_code == 1
    assert "No workspace for backups" in result.output


# -- acceptance 4: remote activity is shown --------------------------------


def make_terminal_console() -> Console:
    return Console(
        file=io.StringIO(),
        record=True,
        force_terminal=True,
        width=120,
        color_system=None,
        _environ={"TERM": "xterm-256color"},
    )


ACTIVITY_CASES = [
    (["diagnose", "run", str(COURSE_ID)], "Diagnosing course 101"),
    (["organize", "unit-order", str(COURSE_ID)], "Planning unit order for course 101"),
    (["discussions", "create", str(COURSE_ID)], "Creating discussions in course 101"),
    (["publish", "content", str(COURSE_ID)], "Publishing course 101"),
    (["audit", str(COURSE_ID)], "Auditing course 101"),
]


@pytest.mark.parametrize(("arguments", "description"), ACTIVITY_CASES, ids=lambda v: str(v)[:30])
def test_remote_commands_show_activity(
    arguments: list[str],
    description: str,
    fake_canvas: FakeCanvas,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_console = make_terminal_console()
    monkeypatch.setattr(design_cli, "console", recorded_console)
    result = run(*arguments)
    assert result.exit_code == 0, result.output
    rendered = recorded_console.export_text()
    assert description in rendered
    assert "Canvas" in rendered
    assert "last 200" in rendered


def test_dates_apply_shows_activity(
    fake_canvas: FakeCanvas, monkeypatch: pytest.MonkeyPatch, schedule_file: Path
) -> None:
    recorded_console = make_terminal_console()
    monkeypatch.setattr(design_cli, "console", recorded_console)
    result = run("dates", "apply", str(COURSE_ID), str(schedule_file))
    assert result.exit_code == 0, result.output
    rendered = recorded_console.export_text()
    assert "Applying dates to course 101" in rendered
    assert "last 200" in rendered
