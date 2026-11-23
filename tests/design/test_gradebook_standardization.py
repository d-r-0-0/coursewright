"""Tests for the gradebook standardization pass."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import (
    BackupFailedError,
    SafeCanvasWriter,
    WriteFailedError,
)
from coursewright.design.organization.gradebook import (
    NoAssignmentGroupsError,
    apply_gradebook_standardization,
    find_grading_scheme_id,
    plan_gradebook_standardization,
)
from coursewright.design.organization.naming import NamingConvention

GROUPS = [
    {
        "id": 1,
        "name": "Assignments",
        "position": 1,
        "group_weight": 50,
        "assignments": [
            {"id": 11, "name": "U02A01: Lab", "assignment_group_id": 1},
            {"id": 12, "name": "Extra credit", "assignment_group_id": 1},
            {"id": 13, "name": "U01T01: Quiz", "assignment_group_id": 1},
        ],
    },
    {
        "id": 2,
        "name": "Discussions",
        "position": 2,
        "group_weight": 50,
        "assignments": [{"id": 21, "name": "U01D01: Intro", "assignment_group_id": 2}],
    },
    {"id": 3, "name": "Survey", "position": 3, "group_weight": 0, "assignments": []},
]


@pytest.fixture
def client():
    canvas = Mock(spec=CanvasClient)
    canvas.get_assignment_groups.return_value = GROUPS
    canvas.get_assignment_group.side_effect = lambda course, group: {"id": group}
    canvas.get_assignment.side_effect = lambda course, assignment: {"id": assignment}
    canvas.get_course.return_value = {"id": 5}
    canvas.update_assignment_group.return_value = {"id": 1}
    canvas.update_assignment.return_value = {"id": 1}
    canvas.delete_assignment_group.return_value = True
    canvas.update_course.return_value = {"id": 5}
    return canvas


def test_plan_keeps_largest_group_and_sorts(client):
    plan = plan_gradebook_standardization(client, 5, grading_scheme_id=77)
    assert plan.keep_group_id == 1
    assert plan.delete_group_ids == [2, 3]
    assert [placement.name for placement in plan.placements] == [
        "U01D01: Intro",
        "U01T01: Quiz",
        "U02A01: Lab",
        "Extra credit",
    ]
    moved = {placement.assignment_id: placement.already_in_group for placement in plan.placements}
    assert moved == {21: False, 13: True, 11: True, 12: True}


def test_no_groups_is_an_error(client):
    client.get_assignment_groups.return_value = []
    with pytest.raises(NoAssignmentGroupsError):
        plan_gradebook_standardization(client, 5)


def test_find_grading_scheme_id():
    standards = [{"id": 1, "title": "Other"}, {"id": 2, "title": "House Scheme"}]
    assert find_grading_scheme_id(standards, "House Scheme") == 2
    assert find_grading_scheme_id(standards, "Missing") is None
    assert find_grading_scheme_id(standards, None) is None


def test_dry_run_writes_nothing(client, tmp_path):
    plan = plan_gradebook_standardization(client, 5, grading_scheme_id=77)
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=False)
    result = apply_gradebook_standardization(plan, writer)
    assert not result.executed
    # 1 group rename + 4 assignments + 2 deletes + 1 course scheme
    assert len(result.outcomes) == 8
    client.update_assignment_group.assert_not_called()
    client.update_assignment.assert_not_called()
    client.delete_assignment_group.assert_not_called()
    client.update_course.assert_not_called()
    assert not (tmp_path / "backups").exists()


def test_execute_applies_every_step_in_order(client, tmp_path):
    plan = plan_gradebook_standardization(client, 5, grading_scheme_id=77)
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    result = apply_gradebook_standardization(plan, writer)
    assert result.executed
    client.update_assignment_group.assert_called_once_with(
        5, 1, {"name": "Coursework", "position": 1, "group_weight": 0}
    )
    client.update_assignment.assert_any_call(5, 21, {"position": 1, "assignment_group_id": 1})
    client.update_assignment.assert_any_call(5, 13, {"position": 2})
    assert client.delete_assignment_group.call_count == 2
    client.update_course.assert_called_once_with(
        5, {"grading_standard_id": 77, "apply_assignment_group_weights": False}
    )
    assert len(list((tmp_path / "backups").glob("*.json"))) == 8


def test_no_scheme_leaves_the_course_alone(client, tmp_path):
    plan = plan_gradebook_standardization(client, 5)
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    apply_gradebook_standardization(plan, writer)
    client.update_course.assert_not_called()


def test_group_name_is_configuration(client, tmp_path):
    naming = NamingConvention(coursework_group_name="All Work")
    plan = plan_gradebook_standardization(client, 5, naming)
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    apply_gradebook_standardization(plan, writer, naming)
    assert client.update_assignment_group.call_args.args[2]["name"] == "All Work"


def test_refusal_stops_the_pass(client, tmp_path):
    client.update_assignment.return_value = None
    plan = plan_gradebook_standardization(client, 5)
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    with pytest.raises(WriteFailedError):
        apply_gradebook_standardization(plan, writer)
    client.delete_assignment_group.assert_not_called()


def test_backup_failure_writes_nothing(client, tmp_path):
    (tmp_path / "backups").write_text("blocks the folder")
    plan = plan_gradebook_standardization(client, 5)
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    with pytest.raises(BackupFailedError):
        apply_gradebook_standardization(plan, writer)
    client.update_assignment_group.assert_not_called()
