"""Guardrailed planning and execution for zeroing ungraded submissions.

Separates read-only inspection (``plan_zero_ungraded``) from Canvas writes
(``execute_zero_ungraded_plan``) so CLI, TUI, and automation callers can
preview targets and surface safety warnings before writing zeros.

Library code only (AGENTS.md 3.8): silent, returns data, and delegates every
write to ``SafeCanvasWriter`` via ``zero_ungraded_submissions``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter
from coursewright.ta.gradeliner.orchestrator import zero_ungraded_submissions

_SUBMITTED_WORKFLOW_STATES = ("submitted", "pending_review")


@dataclass(frozen=True)
class ZeroTargetRow:
    """One ungraded submission targeted to receive a score of 0."""

    assignment_id: int
    assignment_name: str
    user_id: int
    student_name: str
    workflow_state: str
    is_submitted: bool


@dataclass
class AssignmentZeroPlan:
    """Per-assignment breakdown of graded, skipped, and zero-target rows."""

    assignment_id: int
    assignment_name: str
    due_at: datetime | None = None
    published: bool = True
    graded_count: int = 0
    excused_count: int = 0
    skipped_inactive_or_test: int = 0
    skipped_submitted: int = 0
    zero_targets: list[ZeroTargetRow] = field(default_factory=list)
    is_all_ungraded: bool = False

    @property
    def zero_count(self) -> int:
        """Return the number of submissions to zero in this assignment."""
        return len(self.zero_targets)


@dataclass
class ZeroUngradedPlan:
    """Aggregate plan and safety summary across all inspected assignments."""

    course_id: int
    assignments: list[AssignmentZeroPlan] = field(default_factory=list)
    active_user_ids: set[int] | None = None
    skipped_unpublished: int = 0
    total_targets: int = 0
    total_graded: int = 0
    total_excused: int = 0
    total_skipped_submitted: int = 0
    total_skipped_inactive_or_test: int = 0
    all_ungraded_assignment_ids: list[int] = field(default_factory=list)
    include_submitted: bool = False
    past_due_only: bool = True
    include_unpublished: bool = False

    def __post_init__(self) -> None:
        if self.assignments:
            if self.total_targets == 0:
                self.total_targets = sum(len(a.zero_targets) for a in self.assignments)
            if self.total_graded == 0:
                self.total_graded = sum(a.graded_count for a in self.assignments)
            if self.total_excused == 0:
                self.total_excused = sum(a.excused_count for a in self.assignments)
            if self.total_skipped_submitted == 0:
                self.total_skipped_submitted = sum(
                    a.skipped_submitted for a in self.assignments
                )
            if self.total_skipped_inactive_or_test == 0:
                self.total_skipped_inactive_or_test = sum(
                    a.skipped_inactive_or_test for a in self.assignments
                )
            if not self.all_ungraded_assignment_ids:
                self.all_ungraded_assignment_ids = [
                    a.assignment_id for a in self.assignments if a.is_all_ungraded
                ]


def _normalize_utc(moment: datetime) -> datetime:
    """Return *moment* as an aware UTC ``datetime``."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _is_submitted_submission(submission: object) -> bool:
    """Return True when a submission has turned-in student work."""
    is_submitted = getattr(submission, "is_submitted", False) is True
    workflow_state = getattr(submission, "workflow_state", None)
    return is_submitted or workflow_state in _SUBMITTED_WORKFLOW_STATES


def _resolve_student_roster(
    canvas_client: CanvasClient,
    course_id: int,
) -> tuple[set[int] | None, dict[int, str]]:
    """Fetch active student IDs and best-effort display names from Canvas.

    Returns ``(None, {})`` when ``get_course_users`` is unavailable or returns
    a non-list / items lacking positive integer ``.id`` values (such as
    unconfigured mocks).  An empty ``list`` returns ``(set(), {})``.
    """
    get_users = getattr(canvas_client, "get_course_users", None)
    if not callable(get_users):
        return None, {}

    users = get_users(course_id)
    if not isinstance(users, list):
        return None, {}
    if not users:
        return set(), {}

    active_ids: set[int] = set()
    user_names: dict[int, str] = {}
    for user in users:
        user_id = getattr(user, "id", None)
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            return None, {}
        active_ids.add(user_id)
        name = getattr(user, "name", None)
        if isinstance(name, str) and name.strip():
            user_names[user_id] = name
    return active_ids, user_names


def resolve_active_user_ids(
    canvas_client: CanvasClient,
    course_id: int,
) -> set[int] | None:
    """Return active student user IDs for *course_id*, or ``None`` if unavailable."""
    active_ids, _ = _resolve_student_roster(canvas_client, course_id)
    return active_ids


def _resolve_student_name(submission: object, user_names: dict[int, str]) -> str:
    """Extract a student display name from the submission or course roster."""
    user_id = getattr(submission, "user_id", 0)
    user_dict = getattr(submission, "user", None)
    if isinstance(user_dict, dict):
        name = user_dict.get("name")
        if isinstance(name, str) and name.strip():
            return name

    student_name = getattr(submission, "student_name", None)
    if isinstance(student_name, str) and student_name and student_name != f"User {user_id}":
        return student_name

    if isinstance(user_id, int) and user_id in user_names:
        return user_names[user_id]
    return f"User {user_id}"


def _build_assignment_plan(
    assignment: object,
    submissions: Sequence[object],
    *,
    active_user_ids: set[int] | None,
    user_names: dict[int, str],
    include_submitted: bool,
) -> AssignmentZeroPlan:
    """Categorize one assignment's submissions into counts and zero targets."""
    assignment_id = int(assignment.id)
    assignment_name = str(getattr(assignment, "name", f"Assignment {assignment_id}"))
    due_at = getattr(assignment, "due_at", None)
    published = bool(getattr(assignment, "published", True))

    graded_count = 0
    excused_count = 0
    skipped_inactive_or_test = 0
    skipped_submitted = 0
    zero_targets: list[ZeroTargetRow] = []

    for submission in submissions:
        user_id = int(getattr(submission, "user_id", 0))
        if active_user_ids is not None and user_id not in active_user_ids:
            skipped_inactive_or_test += 1
            continue

        if bool(getattr(submission, "excused", False)):
            excused_count += 1
            continue

        if getattr(submission, "score", None) is not None:
            graded_count += 1
            continue

        submitted = _is_submitted_submission(submission)
        if submitted and not include_submitted:
            skipped_submitted += 1
            continue

        raw_state = getattr(submission, "workflow_state", "unsubmitted")
        workflow_state = raw_state if isinstance(raw_state, str) else "unsubmitted"
        zero_targets.append(
            ZeroTargetRow(
                assignment_id=assignment_id,
                assignment_name=assignment_name,
                user_id=user_id,
                student_name=_resolve_student_name(submission, user_names),
                workflow_state=workflow_state,
                is_submitted=submitted,
            )
        )

    is_all_ungraded = graded_count == 0 and len(zero_targets) > 0
    return AssignmentZeroPlan(
        assignment_id=assignment_id,
        assignment_name=assignment_name,
        due_at=due_at,
        published=published,
        graded_count=graded_count,
        excused_count=excused_count,
        skipped_inactive_or_test=skipped_inactive_or_test,
        skipped_submitted=skipped_submitted,
        zero_targets=zero_targets,
        is_all_ungraded=is_all_ungraded,
    )


def plan_zero_ungraded(
    canvas_client: CanvasClient,
    course_id: int,
    *,
    assignment_ids: Sequence[int] | None = None,
    past_due_only: bool = True,
    include_unpublished: bool = False,
    include_submitted: bool = False,
    now: datetime | None = None,
) -> ZeroUngradedPlan:
    """Inspect assignments and build a read-only zero-ungraded execution plan."""
    active_user_ids, user_names = _resolve_student_roster(canvas_client, course_id)
    all_assignments = canvas_client.get_assignments(course_id)
    reference_now = _normalize_utc(now if now is not None else datetime.now(UTC))
    requested_ids = set(assignment_ids) if assignment_ids is not None else None

    skipped_unpublished = 0
    selected_assignments: list[object] = []
    for assignment in all_assignments:
        if requested_ids is not None and assignment.id not in requested_ids:
            continue

        published = bool(getattr(assignment, "published", True))
        if not include_unpublished and not published:
            skipped_unpublished += 1
            continue

        if requested_ids is None and past_due_only:
            due_at = getattr(assignment, "due_at", None)
            if due_at is None or _normalize_utc(due_at) >= reference_now:
                continue

        selected_assignments.append(assignment)

    assignment_plans: list[AssignmentZeroPlan] = []
    for assignment in selected_assignments:
        submissions = canvas_client.get_assignment_submissions(course_id, assignment.id)
        assignment_plans.append(
            _build_assignment_plan(
                assignment,
                submissions,
                active_user_ids=active_user_ids,
                user_names=user_names,
                include_submitted=include_submitted,
            )
        )

    return ZeroUngradedPlan(
        course_id=course_id,
        assignments=assignment_plans,
        active_user_ids=active_user_ids,
        skipped_unpublished=skipped_unpublished,
        total_targets=sum(len(a.zero_targets) for a in assignment_plans),
        total_graded=sum(a.graded_count for a in assignment_plans),
        total_excused=sum(a.excused_count for a in assignment_plans),
        total_skipped_submitted=sum(a.skipped_submitted for a in assignment_plans),
        total_skipped_inactive_or_test=sum(
            a.skipped_inactive_or_test for a in assignment_plans
        ),
        all_ungraded_assignment_ids=[
            a.assignment_id for a in assignment_plans if a.is_all_ungraded
        ],
        include_submitted=include_submitted,
        past_due_only=past_due_only,
        include_unpublished=include_unpublished,
    )


def execute_zero_ungraded_plan(
    canvas_client: CanvasClient,
    writer: SafeCanvasWriter,
    plan: ZeroUngradedPlan,
    *,
    execute: bool = False,
    include_submitted: bool = False,
    on_assignment_start: Callable[[int, int, int], None] | None = None,
    on_assignment_done: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Execute *plan* through ``zero_ungraded_submissions`` and ``SafeCanvasWriter``.

    Only assignments with non-empty ``zero_targets`` are passed to
    ``zero_ungraded_submissions``.
    """
    target_assignment_ids = [
        assignment.assignment_id
        for assignment in plan.assignments
        if assignment.zero_targets
    ]
    return zero_ungraded_submissions(
        canvas_client,
        writer,
        plan.course_id,
        target_assignment_ids,
        execute=execute,
        include_submitted=include_submitted,
        active_user_ids=plan.active_user_ids,
        on_assignment_start=on_assignment_start,
        on_assignment_done=on_assignment_done,
    )
