"""Deleting announcements, modules and assignments, the careful way.

Ported from COURSE_BUILDER cantrips ``delete_announcements``,
``delete_module`` and ``delete_assignment``.  Deletions are the highest-risk
writes in Coursewright: Canvas has no undo, and the shells hold enrolled
students.  Every function here therefore follows one contract:

1. **Dry run by default.**  Without ``execute=True`` the function reads
   Canvas, builds the plan and returns it.  It sends no DELETE and writes no
   backup.
2. **The result lists exactly what will be deleted** (``will_delete``),
   whether or not it was executed, so the person at the keyboard can review
   the list before re-running with ``execute=True``.
3. **Backup first.**  With ``execute=True`` the current state of what is
   about to go is saved as timestamped JSON in the private workspace before
   the first DELETE.  If the backup fails nothing is deleted.
4. **Refuse the dangerous cases.**  A module holding graded items, or an
   assignment students have submitted to, is refused outright.
5. **Never silent.**  A refused delete stops the run and raises
   :class:`PartialDeletionError` carrying a report of what had already gone.

The DELETE requests themselves are made by :class:`SafeCanvasWriter`, which
backs up each object again just before removing it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import (
    BackupFailedError,
    SafeCanvasWriter,
    SafeWriteError,
    WriteOutcome,
    write_backup,
)
from coursewright.design.publishing._strict_reads import (
    CanvasReadError,
    fetch_course_collection,
    fetch_every_announcement,
)

# Module items of these types carry grades, so deleting their module is refused.
GRADED_MODULE_ITEM_TYPES = frozenset({"Assignment", "Quiz", "Discussion"})


class DeletionRefusedError(Exception):
    """The target is unsafe to delete (graded work, student submissions)."""


class DeletionTargetNotFoundError(LookupError):
    """Canvas has no such object, so there is nothing to delete."""


class PartialDeletionError(SafeWriteError):
    """A delete stopped part-way; ``report`` says what was already deleted."""

    def __init__(self, message: str, report: DeletionReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class DeletionTarget:
    """One object that will be (or was) deleted."""

    kind: str
    resource_id: int
    label: str


@dataclass(frozen=True)
class DeletionReport:
    """The plan for a deletion and, when executed, what happened."""

    operation: str
    course_id: int
    will_delete: tuple[DeletionTarget, ...]
    executed: bool
    deleted: tuple[DeletionTarget, ...] = ()
    backup_paths: tuple[Path, ...] = ()
    verified_gone: bool | None = None


def graded_module_items(module_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the module items that make a module unsafe to delete."""
    return [item for item in module_items if item.get("type") in GRADED_MODULE_ITEM_TYPES]


def _delete_targets_in_order(
    operation: str,
    course_id: int,
    targets: list[DeletionTarget],
    delete_one: Callable[[DeletionTarget], WriteOutcome],
    backup_paths: list[Path],
) -> list[DeletionTarget]:
    """Delete each target, stopping at the first failure.

    *delete_one* takes a target and returns the writer's outcome.  Any
    failure raises :class:`PartialDeletionError` with the progress so far.
    """
    deleted: list[DeletionTarget] = []
    for target in targets:
        try:
            outcome = delete_one(target)
        except SafeWriteError as err:
            if isinstance(err, BackupFailedError) and not deleted:
                raise  # nothing was deleted, so there is no partial state to report
            report_so_far = DeletionReport(
                operation=operation,
                course_id=course_id,
                will_delete=tuple(targets),
                executed=True,
                deleted=tuple(deleted),
                backup_paths=tuple(backup_paths),
                verified_gone=False,
            )
            raise PartialDeletionError(
                f"{operation} stopped at {target.kind} {target.resource_id} "
                f"after deleting {len(deleted)} of {len(targets)}: {err}",
                report_so_far,
            ) from err
        if outcome.backup_path is not None:
            backup_paths.append(outcome.backup_path)
        deleted.append(target)
    return deleted


def _verify_deletion(
    client: CanvasClient,
    course_id: int,
    resource_path: str,
    target: DeletionTarget,
    report: DeletionReport,
) -> bool:
    """Never treat a failed readback as proof that a deleted object is gone."""
    try:
        remaining = fetch_course_collection(client, course_id, resource_path)
    except CanvasReadError as err:
        raise PartialDeletionError("Deletion succeeded but verification failed", report) from err
    return target.resource_id not in {record["id"] for record in remaining}


def delete_announcements(
    client: CanvasClient,
    course_id: int,
    backup_directory: Path,
    *,
    execute: bool = False,
) -> DeletionReport:
    """Delete every announcement of a course (preview unless *execute*).

    The whole announcement set is saved as one JSON backup before the first
    delete, and a failed fetch of any page raises
    :class:`~coursewright.design.publishing._strict_reads.CanvasReadError`
    rather than being mistaken for an empty course.

    Raises:
        CanvasReadError: if the announcement list cannot be read completely.
        BackupFailedError: if the backup cannot be saved (nothing deleted).
        PartialDeletionError: if Canvas refuses a delete (the run stops).
    """
    announcements = fetch_every_announcement(client, course_id)
    targets = [
        DeletionTarget("announcement", announcement["id"], announcement.get("title", ""))
        for announcement in announcements
    ]
    if not execute or not targets:
        return DeletionReport("delete_announcements", course_id, tuple(targets), executed=False)

    # One backup of the complete set, before any delete is sent.
    full_backup_path = write_backup(
        backup_directory,
        "delete_announcements",
        f"course {course_id}",
        None,
        announcements,
    )
    backup_paths = [full_backup_path]
    writer = SafeCanvasWriter(client, backup_directory, execute=True)
    deleted = _delete_targets_in_order(
        "delete_announcements",
        course_id,
        targets,
        lambda target: writer.delete_discussion_topic(course_id, target.resource_id),
        backup_paths,
    )
    return DeletionReport(
        "delete_announcements",
        course_id,
        tuple(targets),
        executed=True,
        deleted=tuple(deleted),
        backup_paths=tuple(backup_paths),
    )


def delete_module(
    client: CanvasClient,
    course_id: int,
    module_id: int,
    backup_directory: Path,
    *,
    execute: bool = False,
) -> DeletionReport:
    """Delete one module, keeping the pages and assignments it linked to.

    Refuses a module that holds graded items, so an assignment cannot leave
    student view by accident.  The backup holds the module and its items.

    Raises:
        DeletionTargetNotFoundError: if the module does not exist.
        DeletionRefusedError: if the module holds graded items.
        BackupFailedError / PartialDeletionError: see the module docstring.
    """
    module = client.get_module(course_id, module_id)
    if module is None:
        raise DeletionTargetNotFoundError(f"Module {module_id} not found")
    module_items = fetch_course_collection(client, course_id, f"modules/{module['id']}/items")
    graded_items = graded_module_items(module_items)
    if graded_items:
        raise DeletionRefusedError(f"Refusing: {len(graded_items)} graded item(s) inside")

    target = DeletionTarget("module", module["id"], module.get("name", ""))
    if not execute:
        return DeletionReport("delete_module", course_id, (target,), executed=False)

    writer = SafeCanvasWriter(client, backup_directory, execute=True)
    # Canvas can omit/truncate embedded items, so save the separately fetched
    # complete list as the source cantrip did, before the writer's own backup.
    backup_paths = [
        write_backup(
            backup_directory,
            "delete_module_snapshot",
            f"course {course_id} module {module_id}",
            None,
            {"module": module, "items": module_items},
        )
    ]
    deleted = _delete_targets_in_order(
        "delete_module",
        course_id,
        [target],
        lambda planned: writer.delete_module(course_id, planned.resource_id),
        backup_paths,
    )
    report = DeletionReport(
        "delete_module",
        course_id,
        (target,),
        executed=True,
        deleted=tuple(deleted),
        backup_paths=tuple(backup_paths),
    )
    return replace(report, verified_gone=_verify_deletion(
        client, course_id, "modules", target, report
    ))


def delete_assignment(
    client: CanvasClient,
    course_id: int,
    assignment_id: int,
    backup_directory: Path,
    *,
    execute: bool = False,
) -> DeletionReport:
    """Delete one assignment, refusing if any student has submitted to it.

    Raises:
        DeletionTargetNotFoundError: if the assignment does not exist.
        DeletionRefusedError: if students have submitted work.
        BackupFailedError / PartialDeletionError: see the module docstring.
    """
    assignment = client.get_assignment(course_id, assignment_id)
    if assignment is None:
        raise DeletionTargetNotFoundError(f"Assignment {assignment_id} not found")
    if assignment.get("has_submitted_submissions"):
        raise DeletionRefusedError("Refusing: students have submitted to it")

    target = DeletionTarget("assignment", assignment["id"], assignment.get("name", ""))
    if not execute:
        return DeletionReport("delete_assignment", course_id, (target,), executed=False)

    writer = SafeCanvasWriter(client, backup_directory, execute=True)
    backup_paths: list[Path] = []
    deleted = _delete_targets_in_order(
        "delete_assignment",
        course_id,
        [target],
        lambda planned: writer.delete_assignment(course_id, planned.resource_id),
        backup_paths,
    )
    report = DeletionReport(
        "delete_assignment",
        course_id,
        (target,),
        executed=True,
        deleted=tuple(deleted),
        backup_paths=tuple(backup_paths),
    )
    return replace(report, verified_gone=_verify_deletion(
        client, course_id, "assignments", target, report
    ))
