"""Unit tests for guardrailed zero-ungraded planning and execution."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from coursewright.core.canvas.models import Assignment, CanvasUser, Submission
from coursewright.core.canvas.safe_write import SafeCanvasWriter
from coursewright.ta.gradeliner import (
    AssignmentZeroPlan,
    ZeroTargetRow,
    ZeroUngradedPlan,
    execute_zero_ungraded_plan,
    plan_zero_ungraded,
    zero_ungraded_submissions,
)


def _make_client(
    assignments: list[object],
    submissions_by_assignment: dict[int, list[object]],
    users: object = None,
) -> MagicMock:
    client = MagicMock()
    client.get_assignments.return_value = assignments
    client.get_assignment_submissions.side_effect = (
        lambda _course_id, assignment_id: submissions_by_assignment.get(assignment_id, [])
    )
    if users is not None:
        client.get_course_users.return_value = users
    client.get_submission.return_value = {"score": None}
    client.submit_grade.return_value = True
    return client


def test_public_gradeliner_exports_are_available() -> None:
    assert AssignmentZeroPlan is not None
    assert ZeroTargetRow is not None
    assert ZeroUngradedPlan is not None
    assert callable(plan_zero_ungraded)
    assert callable(execute_zero_ungraded_plan)


def test_zero_ungraded_submissions_skips_submitted_and_inactive_users(tmp_path) -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    client = MagicMock()
    client.get_assignment_submissions.return_value = [
        Submission(id=1, user_id=601, score=None, workflow_state="unsubmitted"),
        Submission(id=2, user_id=602, score=None, workflow_state="submitted"),
        Submission(id=3, user_id=603, score=None, workflow_state="pending_review"),
        Submission(
            id=4,
            user_id=604,
            score=None,
            workflow_state="graded",
            submitted_at=now - timedelta(hours=2),
        ),
        Submission(id=5, user_id=999, score=None, workflow_state="unsubmitted"),
        Submission(id=6, user_id=605, score=None, excused=True),
        Submission(id=7, user_id=606, score=85.0, workflow_state="graded"),
    ]
    client.get_submission.return_value = {"score": None}
    client.submit_grade.return_value = True

    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    zeroed, touched = zero_ungraded_submissions(
        client,
        writer,
        123,
        [2001],
        execute=True,
        active_user_ids={601, 602, 603, 604, 605, 606},
    )

    assert (zeroed, touched) == (1, 1)
    client.submit_grade.assert_called_once_with(123, 2001, 601, 0, None)


def test_zero_ungraded_submissions_include_submitted_writes_submitted_rows(tmp_path) -> None:
    client = MagicMock()
    client.get_assignment_submissions.return_value = [
        Submission(id=1, user_id=601, score=None, workflow_state="unsubmitted"),
        Submission(id=2, user_id=602, score=None, workflow_state="submitted"),
        Submission(id=3, user_id=603, score=None, workflow_state="pending_review"),
        Submission(id=4, user_id=999, score=None, workflow_state="submitted"),
    ]
    client.get_submission.return_value = {"score": None}
    client.submit_grade.return_value = True

    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    zeroed, touched = zero_ungraded_submissions(
        client,
        writer,
        123,
        [2001],
        execute=True,
        include_submitted=True,
        active_user_ids={601, 602, 603},
    )

    assert (zeroed, touched) == (3, 1)
    zeroed_users = [call.args[2] for call in client.submit_grade.call_args_list]
    assert zeroed_users == [601, 602, 603]


def test_zero_ungraded_submissions_supports_simplenamespace_without_state(tmp_path) -> None:
    client = MagicMock()
    client.get_assignment_submissions.return_value = [
        SimpleNamespace(user_id=101, score=None, excused=False),
        SimpleNamespace(user_id=102, score=10.0, excused=False),
    ]
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=False)

    assert zero_ungraded_submissions(client, writer, 42, [1]) == (1, 1)
    client.submit_grade.assert_not_called()


def test_plan_zero_ungraded_filters_assignments_and_categorizes_submissions() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assignments = [
        Assignment(id=1, name="Past Due Lab", due_at=now - timedelta(days=1), published=True),
        Assignment(id=2, name="Unpublished Lab", due_at=now - timedelta(days=2), published=False),
        Assignment(id=3, name="Future Lab", due_at=now + timedelta(days=2), published=True),
        Assignment(id=4, name="No Due Date", due_at=None, published=True),
    ]
    users = [
        CanvasUser(id=101, name="Alex Rivera"),
        CanvasUser(id=102, name="Jordan Patel"),
        CanvasUser(id=103, name="Morgan Lee"),
        CanvasUser(id=104, name="Taylor Kim"),
        CanvasUser(id=105, name="Casey Brooks"),
    ]
    submissions = {
        1: [
            Submission(id=10, user_id=101, score=90.0, workflow_state="graded"),
            Submission(id=11, user_id=102, score=None, excused=True),
            Submission(id=12, user_id=103, score=None, workflow_state="submitted"),
            Submission(id=13, user_id=104, score=None, workflow_state="pending_review"),
            Submission(id=14, user_id=105, score=None, workflow_state="unsubmitted"),
            Submission(id=15, user_id=999, score=None, workflow_state="unsubmitted"),
        ]
    }
    client = _make_client(assignments, submissions, users=users)

    plan = plan_zero_ungraded(client, 123, now=now)

    assert plan.course_id == 123
    assert plan.skipped_unpublished == 1
    assert plan.active_user_ids == {101, 102, 103, 104, 105}
    assert len(plan.assignments) == 1

    item = plan.assignments[0]
    assert item.assignment_id == 1
    assert item.graded_count == 1
    assert item.excused_count == 1
    assert item.skipped_submitted == 2
    assert item.skipped_inactive_or_test == 1
    assert item.is_all_ungraded is False
    assert len(item.zero_targets) == 1
    assert item.zero_targets[0] == ZeroTargetRow(
        assignment_id=1,
        assignment_name="Past Due Lab",
        user_id=105,
        student_name="Casey Brooks",
        workflow_state="unsubmitted",
        is_submitted=False,
    )
    assert plan.total_targets == 1
    assert plan.total_graded == 1
    assert plan.total_excused == 1
    assert plan.total_skipped_submitted == 2
    assert plan.total_skipped_inactive_or_test == 1
    assert plan.all_ungraded_assignment_ids == []
    client.submit_grade.assert_not_called()


def test_plan_zero_ungraded_normalizes_naive_datetimes_to_utc() -> None:
    naive_now = datetime(2026, 3, 10, 12, 0)
    assignments = [
        Assignment(id=1, name="Naive Past", due_at=datetime(2026, 3, 10, 11, 59), published=True),
        Assignment(
            id=2,
            name="Aware Future",
            due_at=datetime(2026, 3, 10, 12, 1, tzinfo=UTC),
            published=True,
        ),
    ]
    client = _make_client(assignments, {1: [], 2: []}, users=[])

    plan = plan_zero_ungraded(client, 123, now=naive_now)

    assert [a.assignment_id for a in plan.assignments] == [1]


def test_plan_zero_ungraded_flags_all_ungraded_even_if_test_student_has_score() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assignments = [
        Assignment(id=1, name="Ungraded Exam", due_at=now - timedelta(hours=3), published=True),
        Assignment(id=2, name="All Submitted", due_at=now - timedelta(hours=3), published=True),
    ]
    users = [CanvasUser(id=101, name="Alex Rivera")]
    submissions = {
        1: [
            Submission(id=10, user_id=101, score=None, workflow_state="unsubmitted"),
            Submission(id=11, user_id=999, score=100.0, workflow_state="graded"),
        ],
        2: [
            Submission(id=12, user_id=101, score=None, workflow_state="submitted"),
        ],
    }
    client = _make_client(assignments, submissions, users=users)

    plan = plan_zero_ungraded(client, 123, now=now)

    by_id = {a.assignment_id: a for a in plan.assignments}
    assert by_id[1].is_all_ungraded is True
    assert by_id[1].graded_count == 0
    assert by_id[1].skipped_inactive_or_test == 1
    assert by_id[2].is_all_ungraded is False
    assert plan.all_ungraded_assignment_ids == [1]


def test_plan_zero_ungraded_explicit_assignment_ids_and_include_flags() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assignments = [
        Assignment(id=1, name="Future Explicit", due_at=now + timedelta(days=5), published=True),
        Assignment(
            id=2,
            name="Unpublished Explicit",
            due_at=now - timedelta(days=1),
            published=False,
        ),
        Assignment(id=3, name="Other Unpublished", due_at=now - timedelta(days=1), published=False),
    ]
    users = [CanvasUser(id=101, name="Alex Rivera")]
    submissions = {
        1: [Submission(id=10, user_id=101, score=None, workflow_state="submitted")],
        2: [Submission(id=11, user_id=101, score=None, workflow_state="unsubmitted")],
        3: [Submission(id=12, user_id=101, score=None, workflow_state="unsubmitted")],
    }
    client = _make_client(assignments, submissions, users=users)

    plan = plan_zero_ungraded(
        client,
        123,
        assignment_ids=[1, 2],
        past_due_only=True,
        include_unpublished=False,
        include_submitted=True,
        now=now,
    )

    assert plan.skipped_unpublished == 1
    assert [a.assignment_id for a in plan.assignments] == [1]
    assert len(plan.assignments[0].zero_targets) == 1
    assert plan.assignments[0].zero_targets[0].is_submitted is True
    assert plan.assignments[0].skipped_submitted == 0

    plan_with_unpub = plan_zero_ungraded(
        client,
        123,
        assignment_ids=[1, 2],
        include_unpublished=True,
        include_submitted=True,
        now=now,
    )
    assert plan_with_unpub.skipped_unpublished == 0
    assert [a.assignment_id for a in plan_with_unpub.assignments] == [1, 2]


@pytest.mark.parametrize(
    "bad_users",
    [
        MagicMock(),
        [MagicMock()],
        [SimpleNamespace(name="No ID")],
        [SimpleNamespace(id=0)],
        [SimpleNamespace(id=True)],
        [CanvasUser(id=101, name="Valid"), SimpleNamespace(id="bad")],
    ],
)
def test_plan_zero_ungraded_treats_invalid_course_users_as_none(bad_users) -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assignments = [
        Assignment(id=1, name="Lab 1", due_at=now - timedelta(days=1), published=True),
    ]
    submissions = {
        1: [Submission(id=10, user_id=999, score=None, workflow_state="unsubmitted")],
    }
    client = _make_client(assignments, submissions, users=bad_users)

    plan = plan_zero_ungraded(client, 123, now=now)

    assert plan.active_user_ids is None
    assert plan.total_targets == 1
    assert plan.total_skipped_inactive_or_test == 0


def test_plan_zero_ungraded_empty_roster_skips_all_submissions() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assignments = [
        Assignment(id=1, name="Lab 1", due_at=now - timedelta(days=1), published=True),
    ]
    submissions = {
        1: [Submission(id=10, user_id=999, score=None, workflow_state="unsubmitted")],
    }
    client = _make_client(assignments, submissions, users=[])

    plan = plan_zero_ungraded(client, 123, now=now)

    assert plan.active_user_ids == set()
    assert plan.total_targets == 0
    assert plan.total_skipped_inactive_or_test == 1


def test_execute_zero_ungraded_plan_only_touches_assignments_with_targets(tmp_path) -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    assignments = [
        Assignment(id=1, name="Has Target", due_at=now - timedelta(days=1), published=True),
        Assignment(id=2, name="All Graded", due_at=now - timedelta(days=1), published=True),
    ]
    users = [
        CanvasUser(id=101, name="Alex Rivera"),
        CanvasUser(id=102, name="Jordan Patel"),
    ]
    submissions = {
        1: [
            Submission(id=10, user_id=101, score=None, workflow_state="unsubmitted"),
            Submission(id=11, user_id=102, score=None, workflow_state="submitted"),
            Submission(id=12, user_id=999, score=None, workflow_state="unsubmitted"),
        ],
        2: [
            Submission(id=20, user_id=101, score=95.0, workflow_state="graded"),
            Submission(id=21, user_id=102, score=88.0, workflow_state="graded"),
        ],
    }
    client = _make_client(assignments, submissions, users=users)

    plan = plan_zero_ungraded(client, 123, now=now)
    client.get_assignment_submissions.reset_mock()

    dry_writer = SafeCanvasWriter(client, tmp_path / "dry_backups", execute=False)
    assert execute_zero_ungraded_plan(client, dry_writer, plan, execute=False) == (1, 1)
    client.submit_grade.assert_not_called()
    assert list((tmp_path / "dry_backups").glob("*.json")) == []

    client.get_assignment_submissions.reset_mock()
    exec_writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    zeroed, touched = execute_zero_ungraded_plan(client, exec_writer, plan, execute=True)

    assert (zeroed, touched) == (1, 1)
    client.get_assignment_submissions.assert_called_once_with(123, 1)
    client.submit_grade.assert_called_once_with(123, 1, 101, 0, None)
    backups = list((tmp_path / "backups").glob("*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["payload"] == {"score": 0}
