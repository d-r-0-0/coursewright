"""Unit tests for honor-system credit planning and execution (synthetic data only)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from coursewright.core.canvas.models import Assignment, CanvasUser, Submission
from coursewright.core.canvas.safe_write import SafeCanvasWriter
from coursewright.ta.gradeliner import (
    HonorCreditPlan,
    execute_honor_credit_plan,
    plan_honor_credit,
)
from coursewright.ta.gradeliner.honor_credit import DEFAULT_HONOR_CREDIT_COMMENT

COURSE_ID = 42


def make_client() -> MagicMock:
    """A course with two honor-system quizzes, one exam and a hidden quiz."""
    client = MagicMock()
    client.get_course_users.return_value = [
        CanvasUser(id=user_id, name=f"Student {user_id}") for user_id in range(101, 108)
    ]
    client.get_assignments.return_value = [
        Assignment(id=1, name="U01Q01: Module Quiz - Basics", points_possible=50.0),
        Assignment(id=2, name="U02Q01: module quiz - Switching", points_possible=50.0),
        Assignment(id=3, name="U02T01: Checkpoint Exam", points_possible=200.0),
        Assignment(id=4, name="U03Q01: Module Quiz - Hid", points_possible=50.0, published=False),
        Assignment(id=5, name="U04Q01: Module Quiz - Practice", points_possible=0.0),
        Assignment(id=6, name="U05Q01: Module Quiz - Big", points_possible=100.0),
    ]
    submissions = {
        1: [
            Submission(id=10, user_id=101, score=None, workflow_state="submitted"),
            Submission(id=11, user_id=102, score=None, workflow_state="pending_review"),
            Submission(id=12, user_id=103, score=None, workflow_state="unsubmitted"),
            Submission(id=13, user_id=104, score=50.0, workflow_state="graded"),
            # Turned in late, on top of the zero a past-due sweep left behind.
            Submission(id=14, user_id=105, score=0.0, workflow_state="submitted"),
            Submission(id=15, user_id=106, score=None, workflow_state="submitted", excused=True),
            # The instructor's own partial grade stands: nothing new was submitted.
            Submission(id=16, user_id=107, score=20.0, workflow_state="graded"),
            # Not on the active roster (dropped student or the test student).
            Submission(id=17, user_id=999, score=None, workflow_state="submitted"),
        ],
        2: [
            Submission(id=20, user_id=101, score=None, workflow_state="submitted"),
            # Resubmitted, but already holding full credit.
            Submission(id=21, user_id=102, score=50.0, workflow_state="submitted"),
        ],
        3: [Submission(id=30, user_id=101, score=None, workflow_state="submitted")],
        6: [Submission(id=60, user_id=101, score=None, workflow_state="submitted")],
    }
    client.get_assignment_submissions.side_effect = (
        lambda _course_id, assignment_id: submissions.get(assignment_id, [])
    )
    client.get_submission.return_value = {"score": None}
    client.submit_grade.return_value = True
    return client


def test_only_submitted_work_awaiting_a_grade_gets_full_credit() -> None:
    plan = plan_honor_credit(make_client(), COURSE_ID, name_contains="Module Quiz")

    first_quiz = plan.assignments[0]
    assert [row.user_id for row in first_quiz.credit_rows] == [101, 102, 105]
    assert {row.points_awarded for row in first_quiz.credit_rows} == {50.0}
    assert first_quiz.not_submitted_count == 1
    assert first_quiz.already_graded_count == 2
    assert first_quiz.excused_count == 1
    assert first_quiz.skipped_inactive_or_test == 1
    assert first_quiz.credit_rows[0].student_name == "Student 101"


def test_selection_matches_names_case_insensitively_and_skips_unusable_assignments() -> None:
    plan = plan_honor_credit(make_client(), COURSE_ID, name_contains="module QUIZ")

    assert [item.assignment_id for item in plan.assignments] == [1, 2, 6]
    assert plan.skipped_unpublished == 1
    assert plan.skipped_without_points == 1
    assert plan.total_credit_rows == 5


def test_points_filter_narrows_the_name_match() -> None:
    plan = plan_honor_credit(
        make_client(), COURSE_ID, name_contains="Module Quiz", points_possible=50
    )
    assert [item.assignment_id for item in plan.assignments] == [1, 2]


def test_assignment_ids_select_exactly_those_assignments() -> None:
    plan = plan_honor_credit(make_client(), COURSE_ID, assignment_ids=[3])
    assert [item.assignment_id for item in plan.assignments] == [3]
    assert plan.assignments[0].credit_rows[0].points_awarded == 200.0


def test_late_resubmission_over_a_zero_is_included_and_reported() -> None:
    plan = plan_honor_credit(make_client(), COURSE_ID, assignment_ids=[1])
    late_row = next(row for row in plan.assignments[0].credit_rows if row.user_id == 105)
    assert late_row.previous_score == 0.0
    assert late_row.replaces_existing_score is True
    assert plan.total_replacing_existing_score == 1


def test_new_only_leaves_existing_scores_alone() -> None:
    plan = plan_honor_credit(
        make_client(), COURSE_ID, assignment_ids=[1], include_existing_scores=False
    )
    assert [row.user_id for row in plan.assignments[0].credit_rows] == [101, 102]
    assert plan.assignments[0].skipped_existing_score == 1
    assert plan.total_replacing_existing_score == 0


def test_resubmission_already_at_full_credit_is_not_rewritten() -> None:
    plan = plan_honor_credit(make_client(), COURSE_ID, assignment_ids=[2])
    assert [row.user_id for row in plan.assignments[0].credit_rows] == [101]
    assert plan.assignments[0].already_graded_count == 1


@pytest.mark.parametrize("selection", [{}, {"name_contains": "   "}, {"assignment_ids": []}])
def test_a_whole_course_is_never_selected_by_accident(selection) -> None:
    client = make_client()
    with pytest.raises(ValueError, match="Name the honor-system assignments"):
        plan_honor_credit(client, COURSE_ID, **selection)
    client.get_assignment_submissions.assert_not_called()


def test_dry_run_writer_sends_nothing(tmp_path) -> None:
    client = make_client()
    plan = plan_honor_credit(client, COURSE_ID, assignment_ids=[1, 2])
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=False)

    assert execute_honor_credit_plan(writer, plan) == (0, 0)
    client.submit_grade.assert_not_called()
    assert not (tmp_path / "backups").exists()


def test_execute_writes_full_points_with_comment_and_backups(tmp_path) -> None:
    client = make_client()
    plan = plan_honor_credit(client, COURSE_ID, assignment_ids=[1, 2])
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)

    assert execute_honor_credit_plan(writer, plan) == (4, 2)
    written = [call.args for call in client.submit_grade.call_args_list]
    assert written == [
        (COURSE_ID, 1, 101, 50.0, DEFAULT_HONOR_CREDIT_COMMENT),
        (COURSE_ID, 1, 102, 50.0, DEFAULT_HONOR_CREDIT_COMMENT),
        (COURSE_ID, 1, 105, 50.0, DEFAULT_HONOR_CREDIT_COMMENT),
        (COURSE_ID, 2, 101, 50.0, DEFAULT_HONOR_CREDIT_COMMENT),
    ]
    assert len(list((tmp_path / "backups").glob("*_submit_grade.json"))) == 4


def test_empty_comment_posts_no_comment(tmp_path) -> None:
    client = make_client()
    plan = plan_honor_credit(client, COURSE_ID, assignment_ids=[2])
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)

    execute_honor_credit_plan(writer, plan, comment="")
    assert client.submit_grade.call_args.args == (COURSE_ID, 2, 101, 50.0, None)


def test_empty_plan_touches_nothing(tmp_path) -> None:
    client = make_client()
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    assert execute_honor_credit_plan(writer, HonorCreditPlan(COURSE_ID)) == (0, 0)
    client.submit_grade.assert_not_called()
