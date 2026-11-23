"""Unlock-hour planning, writing and readback, on a synthetic course."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.models import Assignment
from coursewright.core.canvas.safe_write import SafeCanvasWriter
from coursewright.design.dates.unlock_hour import (
    UnlockHourError,
    apply_unlock_plan,
    assert_course_matches,
    local_iso,
    plan_unlock_changes,
    read_unit_release_dates,
    verify_unlocks,
)

COURSE_ID = 4242
SCHEDULE_TEXT = (
    "# synthetic\n"
    "Unit 01\t09/02/2030\t09/06/2030\t09/13/2030\n"
    "Unit 02\t09/09/2030\t\t09/20/2030\n"
    "Final Exam\t09/23/2030\t\t09/27/2030\n"
)
RELEASE_DATES = {1: date(2030, 9, 2), 2: date(2030, 9, 9)}


@pytest.fixture
def client() -> Mock:
    fake = Mock(spec=CanvasClient)
    fake.get_assignments.return_value = [
        Assignment(id=11, name="Worksheet"),
        Assignment(id=600, name="Introductions"),
    ]
    fake.get_discussion_topics.return_value = [{"id": 500, "assignment_id": 600}]
    fake.get_modules.return_value = [
        {"id": 1, "name": "Unit 1: Start"},
        {"id": 3, "name": "Resources"},
    ]
    fake.get_module_items.return_value = [
        {"type": "Assignment", "title": "Worksheet", "content_id": 11},
        {"type": "Discussion", "title": "Introductions", "content_id": 500},
        {"type": "Page", "title": "Reading", "content_id": 12},
    ]
    return fake


def test_release_dates_read_unit_rows_only() -> None:
    assert read_unit_release_dates(SCHEDULE_TEXT) == RELEASE_DATES


def test_release_dates_without_units_raise() -> None:
    with pytest.raises(UnlockHourError):
        read_unit_release_dates("# nothing here\n")


def test_local_iso_has_no_z_suffix() -> None:
    assert local_iso(date(2030, 9, 2), 8) == "2030-09-02T08:00:00"


def test_plan_covers_modules_and_graded_items(client: Mock) -> None:
    plan = plan_unlock_changes(client, COURSE_ID, RELEASE_DATES, 8)

    assert [(c.target_id, c.unlock_at) for c in plan.module_changes] == [
        (1, "2030-09-02T08:00:00")
    ]
    # The discussion resolves to its assignment id, and names come from Canvas.
    assert [(c.target_id, c.title) for c in plan.item_changes] == [
        (11, "Worksheet"),
        (600, "Introductions"),
    ]


def test_plan_fails_when_a_unit_is_missing_from_the_schedule(client: Mock) -> None:
    with pytest.raises(UnlockHourError, match="Unit 1 is missing"):
        plan_unlock_changes(client, COURSE_ID, {2: date(2030, 9, 9)}, 0)


def test_plan_fails_without_an_assignment_record(client: Mock) -> None:
    client.get_assignments.return_value = []

    with pytest.raises(UnlockHourError, match="No assignment record"):
        plan_unlock_changes(client, COURSE_ID, RELEASE_DATES, 0)


def test_apply_plan_is_a_dry_run_by_default(client: Mock, tmp_path: Path) -> None:
    plan = plan_unlock_changes(client, COURSE_ID, RELEASE_DATES, 8)

    apply_unlock_plan(SafeCanvasWriter(client, tmp_path / "b"), COURSE_ID, plan)

    client.update_module.assert_not_called()
    client.update_assignment.assert_not_called()


def test_apply_plan_executes_with_backups(client: Mock, tmp_path: Path) -> None:
    client.get_module.return_value = {"id": 1}
    client.get_assignment.return_value = {"id": 11}
    client.update_module.return_value = {"id": 1}
    client.update_assignment.return_value = {"id": 11}
    plan = plan_unlock_changes(client, COURSE_ID, RELEASE_DATES, 8)

    apply_unlock_plan(SafeCanvasWriter(client, tmp_path / "b", execute=True), COURSE_ID, plan)

    client.update_module.assert_called_once_with(
        COURSE_ID, 1, {"unlock_at": "2030-09-02T08:00:00"}
    )
    assert client.update_assignment.call_count == 2
    assert len(list((tmp_path / "b").glob("*.json"))) == 3


def test_course_guard_refuses_a_different_course(client: Mock) -> None:
    client.get_course.return_value = {"name": "Synthetic", "course_code": "SYN101.A"}

    assert assert_course_matches(client, COURSE_ID, "SYN101") == "Synthetic"
    with pytest.raises(UnlockHourError, match="refusing to run"):
        assert_course_matches(client, COURSE_ID, "OTHER")


def test_verify_accepts_the_course_zone_across_daylight_time(client: Mock) -> None:
    # 08:00 local on 9/2 (daylight time, UTC-5 in Chicago) is 13:00 UTC.
    client.get_modules.return_value = [
        {"name": "Unit 1: Start", "unlock_at": "2030-09-02T13:00:00Z"}
    ]

    verify_unlocks(client, COURSE_ID, RELEASE_DATES, 8, "America/Chicago")


def test_verify_rejects_a_wrong_hour_and_a_missing_unlock(client: Mock) -> None:
    client.get_modules.return_value = [
        {"name": "Unit 1: Start", "unlock_at": "2030-09-02T12:00:00Z"}
    ]
    with pytest.raises(UnlockHourError, match="which is not"):
        verify_unlocks(client, COURSE_ID, RELEASE_DATES, 8, "America/Chicago")

    client.get_modules.return_value = [{"name": "Unit 1: Start", "unlock_at": None}]
    with pytest.raises(UnlockHourError, match="no readable unlock"):
        verify_unlocks(client, COURSE_ID, RELEASE_DATES, 8, "America/Chicago")
