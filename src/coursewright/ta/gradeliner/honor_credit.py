"""Honor-system credit: full marks for anything a student turned in.

Some assignments are graded on completion alone.  A publisher module quiz
is the usual case: the student takes it on the publisher's site, then
submits a note or screenshot in Canvas, and the instructor awards full
points for having done it.  This module does that grading in two steps,
the same way zeroing ungraded work does:

- ``plan_honor_credit`` reads Canvas and returns who would get credit.
- ``execute_honor_credit_plan`` writes the scores through
  ``SafeCanvasWriter`` (dry run unless the writer was built to execute,
  timestamped backup before every write).

The rule is deliberately narrow.  Only work that is waiting on the
instructor counts: a submission Canvas shows as ``submitted`` or
``pending_review``.  That covers a first submission with no score and a
late resubmission sitting on top of an earlier score (typically the zero
a past-due sweep left behind).  A submission the instructor has already
graded and the student has not touched since is never changed, and
neither are excused or unsubmitted rows.

Library code only (AGENTS.md 3.8): silent, returns data, raises on misuse.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter
from coursewright.ta.gradeliner.zero_ungraded import (
    _resolve_student_name,
    _resolve_student_roster,
)

DEFAULT_HONOR_CREDIT_COMMENT = "Full credit for completing this assignment (honor system)."

# Canvas workflow states that mean "the student turned something in and it
# is waiting on the instructor".
_AWAITING_GRADE_WORKFLOW_STATES = ("submitted", "pending_review")


@dataclass(frozen=True)
class HonorCreditRow:
    """One submission that would receive full credit."""

    assignment_id: int
    assignment_name: str
    user_id: int
    student_name: str
    points_awarded: float
    # The score already in Canvas, when the student resubmitted on top of
    # one (for example a zero from a past-due sweep).  ``None`` for a
    # first submission that has never been scored.
    previous_score: float | None = None

    @property
    def replaces_existing_score(self) -> bool:
        """Return True when awarding credit overwrites an earlier score."""
        return self.previous_score is not None


@dataclass
class AssignmentHonorCreditPlan:
    """Per-assignment breakdown of who gets credit and who is left alone."""

    assignment_id: int
    assignment_name: str
    points_possible: float
    credit_rows: list[HonorCreditRow] = field(default_factory=list)
    already_graded_count: int = 0
    not_submitted_count: int = 0
    excused_count: int = 0
    skipped_inactive_or_test: int = 0
    skipped_existing_score: int = 0


@dataclass
class HonorCreditPlan:
    """Everything a preview needs to show before any score is written."""

    course_id: int
    assignments: list[AssignmentHonorCreditPlan] = field(default_factory=list)
    skipped_unpublished: int = 0
    skipped_without_points: int = 0
    include_existing_scores: bool = True

    @property
    def total_credit_rows(self) -> int:
        """Return how many submissions would receive full credit."""
        return sum(len(item.credit_rows) for item in self.assignments)

    @property
    def total_replacing_existing_score(self) -> int:
        """Return how many of those overwrite a score already in Canvas."""
        return sum(
            row.replaces_existing_score for item in self.assignments for row in item.credit_rows
        )


def _matches_selection(
    assignment: object,
    *,
    requested_ids: set[int] | None,
    name_contains: str | None,
    points_possible: float | None,
) -> bool:
    """Return True when *assignment* is one the instructor asked for."""
    if requested_ids is not None and assignment.id not in requested_ids:
        return False
    if name_contains is not None:
        assignment_name = str(getattr(assignment, "name", ""))
        if name_contains.casefold() not in assignment_name.casefold():
            return False
    if points_possible is not None:
        assignment_points = getattr(assignment, "points_possible", None)
        if assignment_points is None or float(assignment_points) != float(points_possible):
            return False
    return True


def _build_assignment_plan(
    assignment: object,
    submissions: Sequence[object],
    *,
    active_user_ids: set[int] | None,
    user_names: dict[int, str],
    include_existing_scores: bool,
) -> AssignmentHonorCreditPlan:
    """Sort one assignment's submissions into credit rows and skip counts."""
    assignment_id = int(assignment.id)
    assignment_name = str(getattr(assignment, "name", f"Assignment {assignment_id}"))
    full_points = float(assignment.points_possible)
    plan = AssignmentHonorCreditPlan(assignment_id, assignment_name, full_points)

    for submission in submissions:
        user_id = int(getattr(submission, "user_id", 0))
        if active_user_ids is not None and user_id not in active_user_ids:
            plan.skipped_inactive_or_test += 1
            continue
        if bool(getattr(submission, "excused", False)):
            plan.excused_count += 1
            continue

        previous_score = getattr(submission, "score", None)
        workflow_state = getattr(submission, "workflow_state", "unsubmitted")
        if workflow_state not in _AWAITING_GRADE_WORKFLOW_STATES:
            # Either the instructor's grade stands, or nothing was turned in.
            if previous_score is not None:
                plan.already_graded_count += 1
            else:
                plan.not_submitted_count += 1
            continue

        if previous_score is not None:
            if float(previous_score) >= full_points:
                plan.already_graded_count += 1
                continue
            if not include_existing_scores:
                plan.skipped_existing_score += 1
                continue

        plan.credit_rows.append(
            HonorCreditRow(
                assignment_id=assignment_id,
                assignment_name=assignment_name,
                user_id=user_id,
                student_name=_resolve_student_name(submission, user_names),
                points_awarded=full_points,
                previous_score=None if previous_score is None else float(previous_score),
            )
        )
    return plan


def plan_honor_credit(
    canvas_client: CanvasClient,
    course_id: int,
    *,
    assignment_ids: Sequence[int] | None = None,
    name_contains: str | None = None,
    points_possible: float | None = None,
    include_existing_scores: bool = True,
) -> HonorCreditPlan:
    """Inspect Canvas and plan full credit for submitted honor-system work.

    Args:
        canvas_client: Connected Canvas client.
        course_id: The Canvas course to inspect.
        assignment_ids: Only these assignments, when given.
        name_contains: Only assignments whose name contains this text
            (case-insensitive), for example ``"Module Quiz"``.
        points_possible: Only assignments worth exactly this many points.
        include_existing_scores: Also give credit to a resubmission that
            sits on top of an earlier, lower score.  Pass ``False`` to
            touch only submissions that have never been scored.

    Returns:
        A read-only plan; nothing is written.

    Raises:
        ValueError: neither ``assignment_ids`` nor ``name_contains`` was
            given.  Honor credit is never applied to a whole course by
            accident, so the instructor must say which assignments.
    """
    cleaned_name = name_contains.strip() if name_contains else None
    requested_ids = set(assignment_ids) if assignment_ids else None
    if requested_ids is None and not cleaned_name:
        raise ValueError(
            "Name the honor-system assignments: pass assignment IDs or text their names contain."
        )

    active_user_ids, user_names = _resolve_student_roster(canvas_client, course_id)
    plan = HonorCreditPlan(course_id, include_existing_scores=include_existing_scores)

    for assignment in canvas_client.get_assignments(course_id):
        if not _matches_selection(
            assignment,
            requested_ids=requested_ids,
            name_contains=cleaned_name,
            points_possible=points_possible,
        ):
            continue
        if not bool(getattr(assignment, "published", True)):
            plan.skipped_unpublished += 1
            continue
        assignment_points = getattr(assignment, "points_possible", None)
        if assignment_points is None or float(assignment_points) <= 0:
            plan.skipped_without_points += 1
            continue

        submissions = canvas_client.get_assignment_submissions(course_id, assignment.id)
        plan.assignments.append(
            _build_assignment_plan(
                assignment,
                submissions,
                active_user_ids=active_user_ids,
                user_names=user_names,
                include_existing_scores=include_existing_scores,
            )
        )
    return plan


def execute_honor_credit_plan(
    writer: SafeCanvasWriter,
    plan: HonorCreditPlan,
    *,
    comment: str | None = DEFAULT_HONOR_CREDIT_COMMENT,
) -> tuple[int, int]:
    """Award every planned row full credit through ``SafeCanvasWriter``.

    The writer decides whether anything is sent: one built with
    ``execute=False`` records what it would do and writes nothing.

    Args:
        writer: The safe writer (backup first, dry run by default).
        plan: The plan from ``plan_honor_credit``.
        comment: Submission comment posted with each score; ``None`` or
            an empty string posts no comment.

    Returns:
        ``(scores_written, assignments_touched)``; both are 0 in a dry run.
    """
    scores_written = 0
    assignments_touched = 0
    for assignment_plan in plan.assignments:
        written_for_assignment = 0
        for row in assignment_plan.credit_rows:
            outcome = writer.submit_grade(
                plan.course_id,
                row.assignment_id,
                row.user_id,
                row.points_awarded,
                comment or None,
            )
            if outcome.executed:
                written_for_assignment += 1
        scores_written += written_for_assignment
        if written_for_assignment:
            assignments_touched += 1
    return scores_written, assignments_touched
