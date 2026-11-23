"""Set the hour units unlock, without touching any due date.

Ported from the COURSE_BUILDER ``set_unlock_hour`` cantrip.  Release dates
come from the reviewed schedule file rather than from Canvas, so no
local-time conversion is involved when writing and a session that straddles
a daylight-time change cannot drift by an hour.

Unlocking earlier on the same day is safe (nobody arrives at the announced
hour to an empty module), so hour 0 is the conservative setting and the
announced hour is the exact one.

Changes:

* the cantrip's table of course ids, codes and schedule paths is gone; the
  caller passes the course id, and may pass an expected course code that
  :func:`assert_course_matches` checks before anything is planned;
* writes go through :class:`SafeCanvasWriter` (dry run by default, backup
  per object, loud failure), replacing the cantrip's own backup file;
* the readback check takes the course's IANA time zone instead of trying
  two hard-coded UTC offsets.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter
from coursewright.core.config import settings
from coursewright.design.dates.schedule import SCHEDULE_DISPLAY_FORMAT

UNIT_MODULE_PATTERN = re.compile(r"Unit (\d+):")
UNIT_ROW_PATTERN = re.compile(r"^Unit (\d+)$")


class UnlockHourError(RuntimeError):
    """The schedule, the course or Canvas disagrees with the plan."""


@dataclass(frozen=True)
class UnlockChange:
    """One planned unlock write."""

    kind: str  # "module" or "assignment"
    target_id: int
    title: str
    unlock_at: str


@dataclass
class UnlockPlan:
    """Every unlock write for a course at one hour."""

    module_changes: list[UnlockChange] = field(default_factory=list)
    item_changes: list[UnlockChange] = field(default_factory=list)


def read_unit_release_dates(schedule_text: str) -> dict[int, date]:
    """Map each unit number to its release date in a schedule file's text.

    Raises:
        UnlockHourError: when no ``Unit NN`` row can be read.
    """
    release_dates: dict[int, date] = {}

    for row in csv.reader(io.StringIO(schedule_text), delimiter="\t"):
        if not row or row[0].strip().startswith("#") or len(row) < 4:
            continue
        unit_match = UNIT_ROW_PATTERN.match(row[0].strip())
        if not unit_match:
            continue
        release_dates[int(unit_match.group(1))] = datetime.strptime(  # noqa: DTZ007
            row[1].strip(), SCHEDULE_DISPLAY_FORMAT
        ).date()

    if not release_dates:
        raise UnlockHourError("No unit rows parsed from the schedule")
    return release_dates


def local_iso(day: date, hour: int) -> str:
    """Build a Canvas-safe local timestamp with no ``Z`` suffix."""
    return datetime(day.year, day.month, day.day, hour).strftime(  # noqa: DTZ001
        settings.DATE_FORMAT_OUTPUT
    )


def assert_course_matches(client: CanvasClient, course_id: int, expected_course_code: str) -> str:
    """Refuse to continue unless the course's code contains the expected one.

    Returns the course name.

    Raises:
        UnlockHourError: when the course is missing or is a different course.
    """
    course = client.get_course(course_id)
    if not course or expected_course_code not in (course.get("course_code") or ""):
        raise UnlockHourError(
            f"Course {course_id} is not {expected_course_code}; refusing to run"
        )
    return str(course.get("name", ""))


def plan_unlock_changes(
    client: CanvasClient, course_id: int, release_dates: dict[int, date], hour: int
) -> UnlockPlan:
    """Compute the module and item unlock writes.

    Raises:
        UnlockHourError: when a unit module has no schedule row, or a module
            item has no assignment record.
    """
    assignments = client.get_assignments(course_id)
    assignment_ids = {assignment.id for assignment in assignments}
    assignment_names = {assignment.id: assignment.name for assignment in assignments}
    assignment_for_topic = {
        topic["id"]: topic["assignment_id"]
        for topic in client.get_discussion_topics(course_id)
        if topic.get("assignment_id")
    }

    plan = UnlockPlan()

    for module in client.get_modules(course_id):
        unit_match = UNIT_MODULE_PATTERN.match(module.get("name", ""))
        if not unit_match:
            continue
        unit_number = int(unit_match.group(1))
        if unit_number not in release_dates:
            raise UnlockHourError(f"Unit {unit_number} is missing from the schedule")

        desired = local_iso(release_dates[unit_number], hour)
        plan.module_changes.append(UnlockChange("module", module["id"], module["name"], desired))

        for item in client.get_module_items(course_id, module["id"]):
            item_type = item.get("type")
            content_id = item.get("content_id")
            if item_type not in ("Assignment", "Quiz", "Discussion") or not content_id:
                continue
            assignment_id = (
                assignment_for_topic.get(content_id) if item_type == "Discussion" else content_id
            )
            if assignment_id not in assignment_ids:
                raise UnlockHourError(f"No assignment record for {item.get('title')!r}")
            plan.item_changes.append(
                UnlockChange(
                    "assignment",
                    assignment_id,
                    assignment_names.get(assignment_id) or item.get("title", ""),
                    desired,
                )
            )

    return plan


def apply_unlock_plan(writer: SafeCanvasWriter, course_id: int, plan: UnlockPlan) -> None:
    """Write the plan through the safe writer (a dry-run writer sends nothing).

    Raises:
        SafeWriteError: a backup failed or Canvas refused a write.
    """
    for change in plan.module_changes:
        writer.update_module(course_id, change.target_id, {"unlock_at": change.unlock_at})
    for change in plan.item_changes:
        writer.update_assignment(course_id, change.target_id, {"unlock_at": change.unlock_at})


def verify_unlocks(
    client: CanvasClient,
    course_id: int,
    release_dates: dict[int, date],
    hour: int,
    course_timezone: str,
) -> None:
    """Re-read the course and fail if any unit unlock is off the target.

    Canvas answers in UTC, so the expected local time is converted to UTC in
    the course's IANA time zone (for example ``"America/Chicago"``).

    Raises:
        UnlockHourError: when a module's unlock differs from the schedule.
    """
    zone = ZoneInfo(course_timezone)
    utc_zone = ZoneInfo("UTC")

    for module in client.get_modules(course_id):
        unit_match = UNIT_MODULE_PATTERN.match(module.get("name", ""))
        if not unit_match:
            continue
        release_day = release_dates[int(unit_match.group(1))]
        # Canvas reports a UTC "Z" date; that is input here, never output.
        reported = (module.get("unlock_at") or "").replace("Z", "+00:00")
        expected = datetime(
            release_day.year, release_day.month, release_day.day, hour, tzinfo=zone
        ).astimezone(utc_zone)
        try:
            actual = datetime.fromisoformat(reported)
        except ValueError as err:
            raise UnlockHourError(
                f"{module['name']} has no readable unlock time ({module.get('unlock_at')!r})"
            ) from err
        if actual != expected:
            raise UnlockHourError(
                f"{module['name']} unlocks at {module.get('unlock_at')}, "
                f"which is not {release_day} hour {hour}"
            )
