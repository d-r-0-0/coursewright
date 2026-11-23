"""Standardize a course gradebook: one group, sorted assignments, one scheme.

Ported from COURSE_BUILDER ``cantrips/standardize_gradebook.py``.  The pass:

1. keeps the largest assignment group, renames it (default ``Coursework``),
   puts it first and zeroes its weight;
2. moves every assignment into it, positioned by the naming convention's
   gradebook sort key (unit, letter order, sequence, name; unprefixed last);
3. deletes the other, now empty, groups;
4. enables the named grading scheme with group weights off, when the
   convention names one and the caller found its id.

Everything is written through :class:`SafeCanvasWriter`: dry run by default,
a backup before each change, a loud failure on refusal.  Reads happen in
:func:`plan_gradebook_standardization`; it never writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteOutcome
from coursewright.design.organization.naming import DEFAULT_NAMING, NamingConvention


class NoAssignmentGroupsError(Exception):
    """The course has no assignment groups, so there is nothing to standardize."""


@dataclass(frozen=True)
class AssignmentPlacement:
    """Where one assignment ends up."""

    assignment_id: int
    name: str
    position: int
    group_id: int
    already_in_group: bool


@dataclass
class GradebookPlan:
    course_id: int
    keep_group_id: int
    keep_group_name: str
    delete_group_ids: list[int]
    placements: list[AssignmentPlacement]
    grading_scheme_id: int | None
    group_snapshot: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GradebookResult:
    plan: GradebookPlan
    executed: bool
    outcomes: list[WriteOutcome] = field(default_factory=list)


def find_grading_scheme_id(standards: list[dict[str, Any]], title: str | None) -> int | None:
    """Return the id of the grading standard titled *title*, else None.

    *standards* is the course's grading-standard list as Canvas returns it;
    fetching it is the caller's job (the client has no such method yet).
    """
    if title is None:
        return None
    for standard in standards:
        if standard.get("title") == title:
            return standard["id"]
    return None


def plan_gradebook_standardization(
    client: CanvasClient,
    course_id: int,
    naming: NamingConvention = DEFAULT_NAMING,
    grading_scheme_id: int | None = None,
) -> GradebookPlan:
    """Read groups and assignments and plan the pass (no writes).

    Raises:
        NoAssignmentGroupsError: when the course has no groups.
    """
    groups = client.get_assignment_groups(course_id)
    if not groups:
        raise NoAssignmentGroupsError(f"No assignment groups found in course {course_id}.")

    assignments = [assignment for group in groups for assignment in group.get("assignments") or []]
    keep = max(groups, key=lambda group: len(group.get("assignments") or []))
    ordered = sorted(
        assignments, key=lambda assignment: naming.gradebook_sort_key(assignment["name"])
    )
    placements = [
        AssignmentPlacement(
            assignment_id=assignment["id"],
            name=assignment["name"],
            position=position,
            group_id=keep["id"],
            already_in_group=assignment.get("assignment_group_id") == keep["id"],
        )
        for position, assignment in enumerate(ordered, start=1)
    ]
    return GradebookPlan(
        course_id=course_id,
        keep_group_id=keep["id"],
        keep_group_name=keep["name"],
        delete_group_ids=[group["id"] for group in groups if group["id"] != keep["id"]],
        placements=placements,
        grading_scheme_id=grading_scheme_id,
        group_snapshot=[
            {
                "id": group["id"],
                "name": group["name"],
                "position": group.get("position"),
                "weight": group.get("group_weight"),
            }
            for group in groups
        ],
    )


def apply_gradebook_standardization(
    plan: GradebookPlan,
    writer: SafeCanvasWriter,
    naming: NamingConvention = DEFAULT_NAMING,
) -> GradebookResult:
    """Apply (or preview, on a dry-run writer) the planned changes in order.

    Raises:
        BackupFailedError: a backup could not be saved; nothing later runs.
        WriteFailedError: Canvas refused a change; later changes are not applied.
    """
    course_id = plan.course_id
    outcomes = [
        writer.update_assignment_group(
            course_id,
            plan.keep_group_id,
            {"name": naming.coursework_group_name, "position": 1, "group_weight": 0},
        )
    ]
    for placement in plan.placements:
        updates: dict[str, Any] = {"position": placement.position}
        if not placement.already_in_group:
            updates["assignment_group_id"] = placement.group_id
        outcomes.append(writer.update_assignment(course_id, placement.assignment_id, updates))
    outcomes.extend(
        writer.delete_assignment_group(course_id, group_id) for group_id in plan.delete_group_ids
    )
    if plan.grading_scheme_id is not None:
        outcomes.append(
            writer.update_course(
                course_id,
                {
                    "grading_standard_id": plan.grading_scheme_id,
                    "apply_assignment_group_weights": False,
                },
            )
        )
    return GradebookResult(plan=plan, executed=writer.execute, outcomes=outcomes)
