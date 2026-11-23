"""Copying a populated Canvas course into an empty shell, safely.

Ported from COURSE_BUILDER cantrips ``copy_course`` and ``check_migration``.

The copy previews by default, validates both courses, and only with
``execute=True`` saves a timestamped snapshot of the target and starts the
Canvas migration.  The snapshot is written before the migration request.

Changes from the source: the migration is started through
:class:`SafeCanvasWriter` (which also backs up the target's module list and
refuses a silent failure); progress is returned, not printed; the sleep
between polls is injectable so tests do not wait; and the set of module
names a fresh shell may already contain is a parameter instead of one
institution's default module name.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import BackupFailedError, SafeCanvasWriter
from coursewright.design.publishing._strict_reads import (
    fetch_course_collection,
    fetch_every_page_strictly,
)

POLL_INTERVAL_SECONDS = 3


class CourseCopyError(Exception):
    """The course copy stopped; the message says why."""


@dataclass(frozen=True)
class CourseCopyReport:
    """What a copy preview or run found and did."""

    source_course_id: int
    target_course_id: int
    source_name: str
    target_name: str
    source_module_count: int
    source_item_count: int
    target_module_count: int
    target_item_count: int
    executed: bool
    backup_path: Path | None = None
    migration_id: int | None = None
    progress_log: tuple[str, ...] = ()


def snapshot_course(client: CanvasClient, course_id: int) -> dict[str, Any]:
    """Return the metadata needed to validate and audit a course copy."""
    course = client.get_course(course_id)
    if course is None:
        raise CourseCopyError(f"Canvas course {course_id} was not found")

    module_snapshots = [
        {
            "module": module,
            "items": fetch_course_collection(client, course_id, f"modules/{module['id']}/items"),
        }
        for module in fetch_course_collection(client, course_id, "modules")
    ]
    return {
        "course": course,
        "modules": module_snapshots,
        "assignments": fetch_course_collection(client, course_id, "assignments"),
    }


def count_module_items(snapshot: dict[str, Any]) -> int:
    """Count every module item in a course snapshot."""
    return sum(len(entry["items"]) for entry in snapshot["modules"])


def validate_copy_plan(
    source_id: int,
    target_id: int,
    source_snapshot: dict[str, Any],
    target_snapshot: dict[str, Any],
    allowed_default_module_names: Collection[str] = (),
) -> None:
    """Reject a copy plan that could overwrite a populated target shell.

    *allowed_default_module_names* are the modules a brand-new shell already
    has (for example a help module the institution adds to every shell);
    any other module in the target means the target is not empty.

    Raises:
        ValueError: naming the reason the copy is refused.
    """
    if source_id == target_id:
        raise ValueError("Source and target course IDs must be different")

    if len(source_snapshot["modules"]) < 2:
        raise ValueError("Source course does not appear to contain course content")

    target_module_names = {entry["module"].get("name", "") for entry in target_snapshot["modules"]}
    unexpected_target_modules = target_module_names - set(allowed_default_module_names)
    if unexpected_target_modules:
        names = ", ".join(sorted(unexpected_target_modules))
        raise ValueError(f"Target contains non-default modules: {names}")

    if target_snapshot["assignments"]:
        raise ValueError("Target already contains assignments; copy refused")


def save_target_backup(
    source_id: int,
    target_id: int,
    target_snapshot: dict[str, Any],
    backup_directory: Path,
    now: datetime | None = None,
) -> Path:
    """Write a timestamped target snapshot before starting the migration."""
    moment = now or datetime.now()  # noqa: DTZ005 - local wall-clock stamp for a file name
    backup_path = Path(backup_directory) / (
        f"backup_course_copy_{target_id}_{moment:%Y%m%d_%H%M%S_%f}.json"
    )
    payload = {
        "source_course_id": source_id,
        "target_course_id": target_id,
        "captured_at": moment.isoformat(),
        "target_before_copy": target_snapshot,
    }
    try:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with backup_path.open("x", encoding="utf-8") as backup_file:
            json.dump(payload, backup_file, indent=2, default=str)
    except OSError as err:
        raise BackupFailedError(
            f"Could not save backup {backup_path}; the course copy was not started."
        ) from err
    return backup_path


def wait_for_migration(
    client: CanvasClient,
    course_id: int,
    migration_id: int,
    sleep: Callable[[float], None] = time.sleep,
    progress_log: list[str] | None = None,
) -> None:
    """Poll Canvas until a migration completes.

    Raises:
        CourseCopyError: if the migration fails or Canvas stops answering.
    """
    while True:
        sleep(POLL_INTERVAL_SECONDS)
        migration = client.get_content_migration(course_id, migration_id)
        if migration is None:
            raise CourseCopyError("Canvas stopped responding during the course copy")
        state = migration.get("workflow_state", "unknown")
        if progress_log is not None:
            progress_log.append(f"{state:<15} {migration.get('progress', 0):>3}%")
        if state == "completed":
            return
        if state == "failed":
            raise CourseCopyError(f"Canvas migration {migration_id} failed")


def copy_course_content(
    client: CanvasClient,
    source_id: int,
    target_id: int,
    backup_directory: Path,
    *,
    execute: bool = False,
    allowed_default_module_names: Collection[str] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> CourseCopyReport:
    """Validate, then preview or execute one Canvas course copy.

    Reads are always allowed.  Without ``execute`` nothing is written, no
    backup is made and no migration is requested.  With it, the target
    snapshot is saved first; if that fails the migration is never started.

    Raises:
        ValueError: when the plan is unsafe (same course, empty source,
            populated target).
        CourseCopyError: when a course is missing or the migration fails.
        BackupFailedError: when the backup cannot be saved.
        WriteFailedError: when Canvas refuses to start the migration.
    """
    source_snapshot = snapshot_course(client, source_id)
    target_snapshot = snapshot_course(client, target_id)
    validate_copy_plan(
        source_id, target_id, source_snapshot, target_snapshot, allowed_default_module_names
    )

    report_fields: dict[str, Any] = {
        "source_course_id": source_id,
        "target_course_id": target_id,
        "source_name": source_snapshot["course"].get("name", "unnamed source"),
        "target_name": target_snapshot["course"].get("name", "unnamed target"),
        "source_module_count": len(source_snapshot["modules"]),
        "source_item_count": count_module_items(source_snapshot),
        "target_module_count": len(target_snapshot["modules"]),
        "target_item_count": count_module_items(target_snapshot),
    }
    if not execute:
        return CourseCopyReport(**report_fields, executed=False)

    # The full target snapshot first, then the writer's own pre-write backup.
    backup_path = save_target_backup(source_id, target_id, target_snapshot, backup_directory)
    writer = SafeCanvasWriter(client, backup_directory, execute=True)
    outcome = writer.copy_course_content(target_id, source_id)
    migration = outcome.result or {}
    migration_id = migration.get("id")
    if migration_id is None:
        raise CourseCopyError("Canvas did not return a migration ID")

    progress_log = [f"Migration started: {migration_id}"]
    wait_for_migration(client, target_id, int(migration_id), sleep, progress_log)
    progress_log.append("Course copy completed")
    return CourseCopyReport(
        **report_fields,
        executed=True,
        backup_path=backup_path,
        migration_id=int(migration_id),
        progress_log=tuple(progress_log),
    )


def get_migration_status(
    client: CanvasClient, course_id: int, migration_id: int
) -> tuple[str, int] | None:
    """Return one migration's ``(workflow_state, progress percent)``, or ``None``."""
    migration = client.get_content_migration(course_id, migration_id)
    if migration is None:
        return None
    return str(migration.get("workflow_state")), int(migration.get("progress") or 0)


def list_content_migrations(client: CanvasClient, course_id: int) -> list[dict[str, Any]]:
    """List a course's content migrations (``id``, ``workflow_state``, ``progress``).

    Raises:
        CanvasReadError: if the list cannot be read completely.
    """
    migrations = fetch_every_page_strictly(
        client, f"{client.base_url}/api/v1/courses/{course_id}/content_migrations"
    )
    return [
        {
            "id": migration["id"],
            "workflow_state": migration.get("workflow_state"),
            "progress": migration.get("progress", 0),
        }
        for migration in migrations
    ]
