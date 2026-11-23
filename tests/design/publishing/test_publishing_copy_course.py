"""Safety controls of the Canvas course copy.

Ported from COURSE_BUILDER ``tests/test_copy_course.py``.  The fake Canvas
client is kept (it records the migration request) and is wrapped by the real
:class:`SafeCanvasWriter`, so the tests also prove that the writer's own
backup, not just the snapshot, exists before the migration is requested.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from publishing_fakes import make_response

from coursewright.core.canvas.models import Assignment
from coursewright.core.canvas.safe_write import BackupFailedError, WriteFailedError
from coursewright.design.publishing import copy_course
from coursewright.design.publishing.copy_course import (
    CourseCopyError,
    copy_course_content,
    get_migration_status,
    list_content_migrations,
)

DEFAULT_MODULE = "Default Help"


class FakeCanvas:
    """In-memory Canvas client that records content-migration requests."""

    base_url = "https://canvas.example.edu"

    def __init__(self, backup_dir: Path) -> None:
        self.backup_dir = backup_dir
        self.requests: list[str] = []
        self.target_modules = [{"id": 20, "name": DEFAULT_MODULE}]
        self.migration_states = [{"workflow_state": "completed", "progress": 100}]
        self.refuse_migration = False

    def get_course(self, course_id: int) -> dict:
        return {"id": course_id, "name": f"Course {course_id}"}

    def get_modules(self, course_id: int) -> list[dict]:
        if course_id == 1:
            return [{"id": 10, "name": "Start Here"}, {"id": 11, "name": "Unit 01"}]
        return self.target_modules

    def get_module_items(self, course_id: int, module_id: int) -> list[dict]:
        return [{"id": module_id * 10, "title": f"Item {course_id}"}]

    def get_assignments(self, course_id: int) -> list[Assignment]:
        return [Assignment(id=30, name="Assignment")] if course_id == 1 else []

    def _make_request(self, method, url, **kwargs):
        """Serve the strict snapshot reads without changing the source test scenarios."""
        assert method == "GET"
        parts = url.removeprefix(self.base_url + "/api/v1/courses/").split("/")
        course_id = int(parts[0])
        if parts[1] == "assignments":
            payload = [item.model_dump() for item in self.get_assignments(course_id)]
        elif len(parts) == 4:
            payload = self.get_module_items(course_id, int(parts[2]))
        else:
            payload = self.get_modules(course_id)
        return make_response(200, payload)

    def copy_course_content(self, destination_id: int, source_id: int) -> dict | None:
        """The POST: by now both the snapshot and the writer's backup must exist."""
        self.requests.append("POST")
        assert list(self.backup_dir.glob("backup_course_copy_2_*.json"))
        assert list(self.backup_dir.glob("*_copy_course_content.json"))
        return None if self.refuse_migration else {"id": 77}

    def get_content_migration(self, course_id: int, migration_id: int) -> dict | None:
        self.requests.append("GET")
        return self.migration_states.pop(0) if self.migration_states else None


def no_sleep(_seconds: float) -> None:
    return None


def test_dry_run_does_not_write_or_post(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")

    report = copy_course_content(
        canvas, 1, 2, tmp_path / "backups", allowed_default_module_names={DEFAULT_MODULE}
    )

    assert report.executed is False
    assert report.migration_id is None
    assert report.backup_path is None
    assert (report.source_module_count, report.source_item_count) == (2, 2)
    assert (report.target_module_count, report.target_item_count) == (1, 1)
    assert canvas.requests == []
    assert not (tmp_path / "backups").exists()


def test_execute_saves_backup_before_post(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")

    report = copy_course_content(
        canvas,
        1,
        2,
        tmp_path / "backups",
        execute=True,
        allowed_default_module_names={DEFAULT_MODULE},
        sleep=no_sleep,
    )

    assert report.executed is True
    assert report.migration_id == 77
    assert canvas.requests == ["POST", "GET"]
    assert report.backup_path is not None
    assert report.backup_path.exists()
    assert report.progress_log[0] == "Migration started: 77"
    assert report.progress_log[-1] == "Course copy completed"


def test_copy_refuses_a_populated_target(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")
    canvas.target_modules.append({"id": 21, "name": "Unit 01"})

    with pytest.raises(ValueError, match="non-default modules"):
        copy_course_content(
            canvas,
            1,
            2,
            tmp_path / "backups",
            execute=True,
            allowed_default_module_names={DEFAULT_MODULE},
        )

    assert canvas.requests == []
    assert not (tmp_path / "backups").exists()


def test_without_allowed_defaults_any_target_module_is_unexpected(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")

    with pytest.raises(ValueError, match=DEFAULT_MODULE):
        copy_course_content(canvas, 1, 2, tmp_path / "backups")


def test_copy_refuses_same_course_and_empty_source_and_populated_assignments(
    tmp_path: Path,
) -> None:
    canvas = FakeCanvas(tmp_path / "backups")
    with pytest.raises(ValueError, match="must be different"):
        copy_course_content(canvas, 1, 1, tmp_path)
    with pytest.raises(ValueError, match="does not appear to contain"):
        copy_course_content(canvas, 2, 3, tmp_path, allowed_default_module_names={DEFAULT_MODULE})
    canvas.get_assignments = lambda course_id: [Assignment(id=1, name="x")]
    with pytest.raises(ValueError, match="already contains assignments"):
        copy_course_content(canvas, 1, 2, tmp_path, allowed_default_module_names={DEFAULT_MODULE})
    assert canvas.requests == []


def test_backup_failure_means_the_migration_is_never_started(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")
    blocked_directory = tmp_path / "not-a-directory"
    blocked_directory.write_text("a file where the backup folder should be")

    with pytest.raises(BackupFailedError):
        copy_course_content(
            canvas,
            1,
            2,
            blocked_directory,
            execute=True,
            allowed_default_module_names={DEFAULT_MODULE},
        )

    assert canvas.requests == []


def test_canvas_refusing_the_migration_raises(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")
    canvas.refuse_migration = True

    with pytest.raises(WriteFailedError):
        copy_course_content(
            canvas,
            1,
            2,
            tmp_path / "backups",
            execute=True,
            allowed_default_module_names={DEFAULT_MODULE},
            sleep=no_sleep,
        )


def test_a_failed_migration_is_reported(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")
    canvas.migration_states = [
        {"workflow_state": "running", "progress": 40},
        {"workflow_state": "failed"},
    ]

    with pytest.raises(CourseCopyError, match="migration 77 failed"):
        copy_course_content(
            canvas,
            1,
            2,
            tmp_path / "backups",
            execute=True,
            allowed_default_module_names={DEFAULT_MODULE},
            sleep=no_sleep,
        )


def test_canvas_going_silent_while_polling_is_reported(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")
    canvas.migration_states = []

    with pytest.raises(CourseCopyError, match="stopped responding"):
        copy_course_content(
            canvas,
            1,
            2,
            tmp_path / "backups",
            execute=True,
            allowed_default_module_names={DEFAULT_MODULE},
            sleep=no_sleep,
        )


def test_progress_is_polled_until_completion(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")
    canvas.migration_states = [
        {"workflow_state": "running", "progress": 40},
        {"workflow_state": "completed", "progress": 100},
    ]
    progress: list[str] = []
    sleeps: list[float] = []

    copy_course.wait_for_migration(canvas, 2, 77, sleeps.append, progress)

    assert len(sleeps) == 2
    assert "running" in progress[0]
    assert "40%" in progress[0]


def test_a_missing_course_stops_the_copy(tmp_path: Path) -> None:
    canvas = FakeCanvas(tmp_path / "backups")
    canvas.get_course = lambda course_id: None

    with pytest.raises(CourseCopyError, match="was not found"):
        copy_course_content(canvas, 1, 2, tmp_path)


# ----- check_migration (through the real client) -----------------------------


def test_migration_status_reports_state_and_progress(server, client) -> None:
    server.route(
        "GET",
        r"/api/v1/courses/2/content_migrations/77",
        make_response(200, {"workflow_state": "running", "progress": 55}),
    )

    assert get_migration_status(client, 2, 77) == ("running", 55)


def test_migration_status_is_none_when_canvas_has_no_such_migration(server, client) -> None:
    server.route("GET", r"/api/v1/courses/2/content_migrations/9", make_response(404))

    assert get_migration_status(client, 2, 9) is None


def test_migrations_are_listed_with_state_and_progress(server, client) -> None:
    server.route(
        "GET",
        r"/api/v1/courses/2/content_migrations",
        make_response(200, [{"id": 5, "workflow_state": "completed", "progress": 100}]),
    )

    assert list_content_migrations(client, 2) == [
        {"id": 5, "workflow_state": "completed", "progress": 100}
    ]
