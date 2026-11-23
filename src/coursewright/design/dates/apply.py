"""Apply unlock and due dates from a schedule to a course's content.

Ported from COURSE_BUILDER ``scripts/dates/apply_dates.py``.  The schedule
format is documented in :mod:`coursewright.design.dates.schedule`.

What changed in the port, and why:

* every write goes through :class:`SafeCanvasWriter` (rule 3.11): dry run
  unless the writer was built with ``execute=True``, a timestamped backup of
  each object before it is changed, and a loud failure when Canvas refuses;
* the course-wide JSON backup, the course search, the prompts and the
  printing are gone: library code is silent (rule 3.8) and ``ui/`` does the
  talking.  The returned :class:`DateApplicationReport` lists every planned
  change so the UI can render the preview;
* the discussion-topic to assignment lookup reads the discussion topics
  (``assignment_id``) instead of a nested field the typed assignment model
  does not keep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteFailedError

GRADEABLE_ITEM_TYPES = ("Assignment", "Quiz", "Discussion")


@dataclass(frozen=True)
class PlannedDateChange:
    """One date write: what it touches and the values it sends."""

    kind: str  # "Module", "Assignment", "Quiz" or "Discussion"
    title: str
    updates: dict[str, str]
    applied: bool  # False in a dry run


@dataclass
class DateApplicationReport:
    """What applying a schedule did (or, in a dry run, would do)."""

    modules_updated: int = 0
    assignments_updated: int = 0
    quizzes_updated: int = 0
    discussions_updated: int = 0
    modules_skipped: int = 0
    skipped_module_names: list[str] = field(default_factory=list)
    changes: list[PlannedDateChange] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_stats(self) -> dict[str, Any]:
        """Return the counters in the shape the source script used."""
        return {
            "modules_updated": self.modules_updated,
            "assignments_updated": self.assignments_updated,
            "quizzes_updated": self.quizzes_updated,
            "discussions_updated": self.discussions_updated,
            "modules_skipped": self.modules_skipped,
            "errors": list(self.errors),
        }


def find_schedule_key(module_name: str, schedule: dict[str, dict[str, Any]]) -> str | None:
    """Match a module to a schedule row by its ``Unit NN`` name."""
    for key in schedule:
        if module_name.startswith(key) or key in module_name:
            return key
    return None


def build_discussion_topic_to_assignment_map(
    client: CanvasClient, course_id: int
) -> dict[int, int]:
    """Map each graded discussion's topic id to its assignment id.

    Discussion module items carry the topic id, but dates are written to the
    discussion's assignment.
    """
    return {
        topic["id"]: topic["assignment_id"]
        for topic in client.get_discussion_topics(course_id)
        if topic.get("assignment_id")
    }


def apply_dates_to_course(
    client: CanvasClient,
    writer: SafeCanvasWriter,
    course_id: int,
    schedule: dict[str, dict[str, Any]],
) -> DateApplicationReport:
    """Apply schedule dates to modules and their assignments, quizzes and discussions.

    Whether Canvas is written is decided by ``writer.execute``; with the
    default dry-run writer nothing is sent and the report says what would be.

    Raises:
        BackupFailedError: a backup could not be saved; nothing more is written.
    """
    report = DateApplicationReport()
    applied = writer.execute

    discussion_topic_to_assignment = build_discussion_topic_to_assignment_map(client, course_id)

    for module in client.get_modules(course_id):
        module_name = module["name"]
        module_id = module["id"]

        schedule_key = find_schedule_key(module_name, schedule)
        if not schedule_key:
            report.modules_skipped += 1
            report.skipped_module_names.append(module_name)
            continue

        unlock_at = schedule[schedule_key]["unlock_at"]
        due_at = schedule[schedule_key]["due_at"]

        try:
            writer.update_module(course_id, module_id, {"unlock_at": unlock_at})
        except WriteFailedError as err:
            report.errors.append(f"Failed to update module {module_name}: {err}")
            continue
        report.modules_updated += 1
        report.changes.append(
            PlannedDateChange("Module", module_name, {"unlock_at": unlock_at}, applied)
        )

        for item in client.get_module_items(course_id, module_id):
            item_type = item.get("type")
            item_title = item.get("title", "Untitled")
            content_id = item.get("content_id")

            # Only gradeable items carry dates.
            if not content_id or item_type not in GRADEABLE_ITEM_TYPES:
                continue

            dates = {"due_at": due_at, "unlock_at": unlock_at}
            try:
                if item_type == "Quiz":
                    writer.update_quiz(course_id, content_id, dates)
                elif item_type == "Assignment":
                    writer.update_assignment(course_id, content_id, dates)
                else:
                    # content_id is the discussion topic id; dates go on its assignment.
                    assignment_id = discussion_topic_to_assignment.get(content_id)
                    if not assignment_id:
                        raise ValueError(
                            f"Could not find assignment for discussion topic {content_id}"
                        )
                    writer.update_assignment(course_id, assignment_id, dates)
            except (WriteFailedError, ValueError) as err:
                report.errors.append(f"Failed to update {item_type} '{item_title}': {err}")
                continue

            if item_type == "Quiz":
                report.quizzes_updated += 1
            elif item_type == "Assignment":
                report.assignments_updated += 1
            else:
                report.discussions_updated += 1
            report.changes.append(PlannedDateChange(item_type, item_title, dates, applied))

    return report
