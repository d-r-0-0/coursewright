"""Phase 1 course diagnostics: structure counts and common problems.

Library version of COURSE_BUILDER ``scripts/diagnostic/diagnose_course.py``.
The source printed a report as it went; here ``diagnose_course`` returns a
``CourseDiagnostics`` model and the caller decides how to show or save it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from coursewright.design.diagnostics.models import (
    CourseDiagnostics,
    CourseNotFoundError,
    PastDueAssignment,
)
from coursewright.design.diagnostics.reading import ReadOnlyCanvas

# Thresholds the source hard-coded in its "common issues" check.
MINIMUM_EXPECTED_MODULES = 4
MAJORITY_FRACTION = 0.5

ISSUE_UNPUBLISHED_MODULES = "Unpublished modules found"
ISSUE_MISSING_DUE_DATES = "Assignments without due dates"
ISSUE_FEW_MODULES = "Very few modules (expected at least 4 intro + content modules)"
ISSUE_NO_DISCUSSIONS = "No discussions found (expected ~7 for typical course)"
ISSUE_MOSTLY_UNPUBLISHED = "More than 50% of modules are unpublished"
ISSUE_MOSTLY_UNDATED = "More than 50% of assignments missing due dates"


def _as_utc(moment: datetime) -> datetime:
    """Treat a naive datetime as UTC so it can be compared with aware ones."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def diagnose_course(
    canvas: ReadOnlyCanvas,
    course: dict[str, Any],
    now: datetime | None = None,
) -> CourseDiagnostics:
    """Analyse one course's modules, items and assignments.

    Args:
        canvas: A read-only Canvas client.
        course: The course record (needs ``id`` and ``name``).
        now: The moment to compare due dates with (default: current UTC
            time).  Injectable so tests are deterministic.

    Deliberate changes from the source: module items are fetched once, not
    twice, and due dates are compared as timezone-aware values.  The source
    compared an aware date with a naive ``now``, which raised ``TypeError``
    inside a bare ``except`` and so never reported any past-due assignment.
    """
    moment = _as_utc(now) if now else datetime.now(UTC)
    course_id = course["id"]

    modules = canvas.get_modules(course_id)
    total_items = 0
    unpublished_modules: list[str] = []
    modules_without_unlock_dates: list[str] = []
    content_types: dict[str, int] = {}

    for module in modules:
        module_items = canvas.get_module_items(course_id, module["id"])
        total_items += len(module_items)
        if not module.get("published"):
            unpublished_modules.append(module["name"])
        if not module.get("unlock_at"):
            modules_without_unlock_dates.append(module["name"])
        for item in module_items:
            item_type = item.get("type", "Unknown")
            content_types[item_type] = content_types.get(item_type, 0) + 1

    assignments = canvas.get_assignments(course_id)
    missing_due_dates: list[str] = []
    past_due_dates: list[PastDueAssignment] = []
    for assignment in assignments:
        if not assignment.due_at:
            missing_due_dates.append(assignment.name)
        elif _as_utc(assignment.due_at) < moment:
            past_due_dates.append(
                PastDueAssignment(name=assignment.name, due_at=assignment.due_at.isoformat())
            )

    issues: list[str] = []
    if unpublished_modules:
        issues.append(ISSUE_UNPUBLISHED_MODULES)
    if missing_due_dates:
        issues.append(ISSUE_MISSING_DUE_DATES)
    if len(modules) < MINIMUM_EXPECTED_MODULES:
        issues.append(ISSUE_FEW_MODULES)
    if content_types.get("Discussion", 0) == 0:
        issues.append(ISSUE_NO_DISCUSSIONS)
    if len(unpublished_modules) > len(modules) * MAJORITY_FRACTION:
        issues.append(ISSUE_MOSTLY_UNPUBLISHED)
    if len(missing_due_dates) > len(assignments) * MAJORITY_FRACTION:
        issues.append(ISSUE_MOSTLY_UNDATED)

    return CourseDiagnostics(
        course_id=course_id,
        course_name=course["name"],
        workflow_state=course.get("workflow_state", "unknown"),
        timestamp=moment.isoformat(),
        total_modules=len(modules),
        total_items=total_items,
        total_assignments=len(assignments),
        unpublished_modules=unpublished_modules,
        modules_without_unlock_dates=modules_without_unlock_dates,
        missing_due_dates=missing_due_dates,
        past_due_dates=past_due_dates,
        content_types=content_types,
        issues=issues,
    )


def diagnose_course_by_id(
    canvas: ReadOnlyCanvas, course_id: int, now: datetime | None = None
) -> CourseDiagnostics:
    """Fetch the course record, then diagnose it.

    Raises:
        CourseNotFoundError: when Canvas does not return the course.
    """
    course = canvas.get_course(course_id)
    if not course:
        raise CourseNotFoundError(f"Canvas course {course_id} was not found.")
    return diagnose_course(canvas, course, now=now)
