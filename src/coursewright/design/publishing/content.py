"""Phase 5 publishing: make modules and their items visible to students.

Ported from COURSE_BUILDER ``scripts/publishing/publish_content.py``.  The
source mixed this logic with prompts, prints and its own backup code; here
the library part returns a report and the prompts (strategy menu, checklist,
final confirmation) are left to ``ui/``.

Previews by default.  With ``execute=True`` a snapshot of the course's
modules is saved first (abort if it fails), then each module and item is
published through :class:`SafeCanvasWriter`, which backs each one up again.
Content with unlock dates stays locked until the date: publishing only
makes it *eligible* to be seen.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import (
    BackupFailedError,
    SafeCanvasWriter,
    WriteFailedError,
    write_backup,
)
from coursewright.design.publishing._strict_reads import fetch_course_collection

# What the instructor should confirm before publishing; ``ui/`` shows it.
PRE_PUBLISH_CHECKLIST = (
    "Course content is organized correctly",
    "Discussions are created in appropriate modules",
    "Dates are applied and verified in student view",
    "Syllabus and policies are up to date",
    "All content has been reviewed for accuracy",
    "You have a backup of the current state",
)


@dataclass(frozen=True)
class PublishingItem:
    """One module item that is, or would be, published."""

    item_id: int
    title: str


@dataclass(frozen=True)
class ModulePublishing:
    """What happens to one selected module."""

    module_id: int
    name: str
    publish_module: bool
    items_to_publish: tuple[PublishingItem, ...]
    already_published_item_count: int


@dataclass
class PublishingReport:
    """The publishing plan and, when executed, its outcome."""

    course_id: int
    executed: bool
    modules: list[ModulePublishing] = field(default_factory=list)
    modules_skipped: int = 0
    modules_published: int = 0
    items_published: int = 0
    errors: list[str] = field(default_factory=list)
    backup_paths: list[Path] = field(default_factory=list)

    @property
    def modules_to_publish(self) -> int:
        """Modules the plan would publish (counted whether or not executed)."""
        return sum(1 for planned in self.modules if planned.publish_module)

    @property
    def items_to_publish(self) -> int:
        """Items the plan would publish (counted whether or not executed)."""
        return sum(len(planned.items_to_publish) for planned in self.modules)


def first_module_names(modules: list[dict[str, Any]], count: int) -> list[str]:
    """Names of the first *count* modules (the progressive-release strategy).

    Raises:
        ValueError: if *count* is not between 1 and the number of modules.
    """
    if not 1 <= count <= len(modules):
        raise ValueError(f"Invalid number: {count} (choose 1-{len(modules)})")
    return [module["name"] for module in modules[:count]]


def plan_content_publishing(
    client: CanvasClient,
    course_id: int,
    module_names: Collection[str] | None = None,
) -> PublishingReport:
    """Work out what publishing would do, without writing anything.

    *module_names* limits the plan to those modules; ``None`` means all.
    """
    report = PublishingReport(course_id=course_id, executed=False)
    for module in fetch_course_collection(client, course_id, "modules"):
        if module_names is not None and module["name"] not in module_names:
            report.modules_skipped += 1
            continue
        items = fetch_course_collection(client, course_id, f"modules/{module['id']}/items")
        unpublished_items = [item for item in items if not item.get("published", False)]
        report.modules.append(
            ModulePublishing(
                module_id=module["id"],
                name=module["name"],
                publish_module=not module.get("published", False),
                items_to_publish=tuple(
                    PublishingItem(item["id"], item.get("title", "Untitled"))
                    for item in unpublished_items
                ),
                already_published_item_count=len(items) - len(unpublished_items),
            )
        )
    return report


def publish_course_content(
    client: CanvasClient,
    course_id: int,
    backup_directory: Path,
    *,
    module_names: Collection[str] | None = None,
    execute: bool = False,
) -> PublishingReport:
    """Preview or perform publishing of modules and their items.

    A refused publish of one module or item is recorded in ``errors`` and the
    run goes on (as in the source); a failed *backup* aborts the whole run.

    Raises:
        BackupFailedError: if the pre-publish snapshot or a per-object
            backup cannot be saved.
    """
    report = plan_content_publishing(client, course_id, module_names)
    if not execute:
        return report

    report.executed = True
    snapshot_path = write_backup(
        backup_directory,
        "publish_course_content",
        f"course {course_id}",
        {"module_names": None if module_names is None else sorted(module_names)},
        {"course_id": course_id, "modules": fetch_course_collection(client, course_id, "modules")},
    )
    report.backup_paths.append(snapshot_path)

    writer = SafeCanvasWriter(client, backup_directory, execute=True)
    for planned in report.modules:
        if planned.publish_module:
            try:
                outcome = writer.set_published("module", course_id, planned.module_id, True)
            except WriteFailedError as err:
                report.errors.append(f"Failed to publish module {planned.name}: {err}")
                continue  # its items stay as they were
            report.modules_published += 1
            report.backup_paths.append(_backup_of(outcome))
        for item in planned.items_to_publish:
            try:
                outcome = writer.set_published(
                    "module_item", course_id, item.item_id, True, module_id=planned.module_id
                )
            except WriteFailedError as err:
                report.errors.append(f"Failed to publish item '{item.title}': {err}")
                continue
            report.items_published += 1
            report.backup_paths.append(_backup_of(outcome))
    return report


def _backup_of(outcome: Any) -> Path:
    """The backup path of an executed outcome (always present when executed)."""
    if outcome.backup_path is None:
        raise BackupFailedError("An executed write reported no backup.")
    return outcome.backup_path
