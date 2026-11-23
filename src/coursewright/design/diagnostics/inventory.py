"""Course, module and module-item inventories.

Library versions of the ``list_courses``, ``list_modules``,
``list_module_items``, ``inspect_course`` and ``scan_course`` cantrips,
plus the course lookup from ``diagnose_course``.  They return models and
never print.  Everything here is read-only.

Deliberate change: the source ``scan_course --remove --live`` deleted
module items.  Phase 1 may not write to Canvas, so only the dry-run half
(listing the matches) is ported; deletion belongs to the organization
phase behind ``SafeCanvasWriter``.
"""

from __future__ import annotations

import re
from typing import Any

from coursewright.design.diagnostics.models import (
    CourseInspection,
    CourseNotFoundError,
    CourseScan,
    CourseSummary,
    ItemMatch,
    ModuleItemRecord,
    ModuleRecord,
)
from coursewright.design.diagnostics.reading import ReadOnlyCanvas

MINIMUM_SEARCH_PATTERN_LENGTH = 2
MAXIMUM_SEARCH_PATTERN_LENGTH = 100


def _require_course(canvas: ReadOnlyCanvas, course_id: int) -> dict[str, Any]:
    """Return the course record or raise ``CourseNotFoundError``."""
    course = canvas.get_course(course_id)
    if not course:
        raise CourseNotFoundError(f"Canvas course {course_id} was not found.")
    return course


def _item_record(raw_item: dict[str, Any]) -> ModuleItemRecord:
    return ModuleItemRecord(
        id=raw_item.get("id"),
        title=raw_item.get("title", ""),
        type=raw_item.get("type", ""),
        published=raw_item.get("published"),
        external_url=raw_item.get("external_url"),
    )


def _module_record(raw_module: dict[str, Any], raw_items: list[dict[str, Any]]) -> ModuleRecord:
    return ModuleRecord(
        id=raw_module["id"],
        name=raw_module.get("name", ""),
        published=bool(raw_module.get("published")),
        unlock_at=raw_module.get("unlock_at"),
        items=[_item_record(raw_item) for raw_item in raw_items],
    )


def validate_search_pattern(
    pattern: str,
    minimum_length: int = MINIMUM_SEARCH_PATTERN_LENGTH,
    maximum_length: int = MAXIMUM_SEARCH_PATTERN_LENGTH,
) -> None:
    """Raise ``ValueError`` when a course-name pattern is unusable.

    The source ``validate_input`` logged a warning and returned False;
    a library raises instead so callers cannot ignore it.
    """
    if not pattern:
        raise ValueError("Search pattern is empty.")
    if len(pattern) < minimum_length:
        raise ValueError(f"Search pattern too short (minimum {minimum_length} characters).")
    if len(pattern) > maximum_length:
        raise ValueError(f"Search pattern too long (maximum {maximum_length} characters).")


def find_courses(canvas: ReadOnlyCanvas, pattern: str) -> list[CourseSummary]:
    """Find courses whose name or code contains *pattern* (case-insensitive).

    Choosing among several matches is a prompt, so it is left to ``ui/``.
    """
    validate_search_pattern(pattern)
    return [_course_summary(raw) for raw in canvas.find_course_by_name(pattern)]


def _course_summary(raw_course: dict[str, Any]) -> CourseSummary:
    term = raw_course.get("term") or {}
    return CourseSummary(
        id=raw_course["id"],
        name=raw_course.get("name", ""),
        course_code=raw_course.get("course_code", ""),
        term_name=term.get("name", "N/A"),
        workflow_state=raw_course.get("workflow_state", ""),
    )


def list_courses(canvas: ReadOnlyCanvas, search: str | None = None) -> list[CourseSummary]:
    """List courses sorted by id, optionally filtered by name substring."""
    summaries = [_course_summary(raw) for raw in canvas.get_courses()]
    if search:
        summaries = [s for s in summaries if search.lower() in s.name.lower()]
    return sorted(summaries, key=lambda summary: summary.id)


def list_modules(
    canvas: ReadOnlyCanvas, course_id: int, include_items: bool = False
) -> list[ModuleRecord]:
    """Modules with id, publish state and unlock date, optionally with items.

    Raises:
        CourseNotFoundError: when the course does not exist.
    """
    _require_course(canvas, course_id)
    return [
        _module_record(
            raw_module,
            canvas.get_module_items(course_id, raw_module["id"]) if include_items else [],
        )
        for raw_module in canvas.get_modules(course_id)
    ]


def list_module_items(
    canvas: ReadOnlyCanvas, course_id: int, unpublished_only: bool = False
) -> list[ModuleRecord]:
    """Every module with its items; optionally only unpublished items.

    A missing ``published`` flag counts as published, as in the source.
    """
    _require_course(canvas, course_id)
    modules: list[ModuleRecord] = []
    for raw_module in canvas.get_modules(course_id):
        record = _module_record(raw_module, canvas.get_module_items(course_id, raw_module["id"]))
        if unpublished_only:
            record.items = [item for item in record.items if item.published is False]
        modules.append(record)
    return modules


def inspect_course(canvas: ReadOnlyCanvas, course_id: int) -> CourseInspection:
    """Course facts plus its module tree, using the items Canvas embeds.

    Raises:
        CourseNotFoundError: when the course does not exist.  (The source
            skipped the header and carried on; a library should say so.)
    """
    course = _require_course(canvas, course_id)
    modules = [
        _module_record(raw_module, raw_module.get("items", []))
        for raw_module in canvas.get_modules(course_id)
    ]
    return CourseInspection(
        course_id=course_id,
        name=course.get("name") or "",
        term_name=(course.get("term") or {}).get("name"),
        workflow_state=course.get("workflow_state"),
        modules=modules,
    )


def scan_course(
    canvas: ReadOnlyCanvas, course_id: int, title_pattern: str | None = None
) -> CourseScan:
    """Terse module -> item tree; list items whose title matches a regex.

    This only reports matches.  It never removes anything.
    """
    compiled = re.compile(title_pattern, re.IGNORECASE) if title_pattern else None
    modules: list[ModuleRecord] = []
    matches: list[ItemMatch] = []
    total_items = 0
    for raw_module in canvas.get_modules(course_id):
        record = _module_record(raw_module, canvas.get_module_items(course_id, raw_module["id"]))
        total_items += len(record.items)
        for item in record.items:
            if compiled and item.id is not None and compiled.search(item.title):
                matches.append(ItemMatch(module_id=record.id, item_id=item.id, title=item.title))
        modules.append(record)
    return CourseScan(
        course_id=course_id, modules=modules, total_items=total_items, matches=matches
    )
