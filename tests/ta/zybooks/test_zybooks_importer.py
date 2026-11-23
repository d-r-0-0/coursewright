"""Synthetic CSV import tests; no browser, real students, or network writes."""

import csv
import json
import os
import subprocess
import sys
from contextlib import suppress
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from coursewright.core.inbox import CandidateLocation
from coursewright.ta.zybooks import importer
from coursewright.ta.zybooks.importer import (
    CsvImportCoordinator,
    ImportProblem,
    MappingCache,
    UnitMapping,
    calculate,
    format_sections,
    parse_sections,
    propose_students,
    propose_units,
    read_snapshot,
)


def write_csv(path, labs=(100, 90, 80, 70, 60, 50, 40), pa=80, ca=60):
    sections = ["2.1"] + [f"2.{index + 12}" for index in range(len(labs))]
    columns = []
    for category in ("Participation", "Challenge", "Lab"):
        for section in sections:
            maximum, score = 0, "N/A"
            if section == "2.1" and category in {"Participation", "Challenge"}:
                maximum, score = (10, pa) if category == "Participation" else (5, ca)
            elif section != "2.1" and category == "Lab":
                index = int(section.split(".")[1]) - 12
                maximum, score = (3 if index % 2 else 10), labs[index]
            columns.append((f"{section} - {category} ({maximum})", score))
    maximum = sum(3 if index % 2 else 10 for index in range(len(labs)))
    headers = [
        "Last name",
        "First name",
        "Primary email",
        "Class section",
        f"Total ({15 + maximum})",
        "Participation total (10)",
        "Challenge total (5)",
        f"Lab total ({maximum})",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers + [name for name, _ in columns])
        writer.writerow(
            ["Example", "Student", "example@example.invalid", "", 70, pa, ca, 70]
            + [score for _, score in columns]
        )
    return path


def write_chapter_one_csv(path, participation=(100, 50, 0), maxima=(13, 23, 12)):
    """Chapter 1 of a real export: participation points only, no challenges or labs."""
    sections = [f"1.{index + 1}" for index in range(len(participation))]
    columns = []
    for category in ("Participation", "Challenge", "Lab"):
        for index, section in enumerate(sections):
            if category == "Participation":
                score = participation[index] if maxima[index] else "N/A"
                columns.append((f"{section} - Participation ({maxima[index]})", score))
            else:
                columns.append((f"{section} - {category} (0)", "N/A"))
    total = sum(maxima)
    headers = [
        "Last name",
        "First name",
        "Primary email",
        "Class section",
        f"Total ({total})",
        f"Participation total ({total})",
        "Challenge total (0)",
        "Lab total (0)",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers + [name for name, _ in columns])
        writer.writerow(
            ["Example", "Student", "example@example.invalid", "", 50, 50, 0, 0]
            + [score for _, score in columns]
        )
    return path


def test_participation_only_unit_carries_the_whole_activity_target(tmp_path):
    snapshot = read_snapshot(write_chapter_one_csv(tmp_path / "Example_report_1.csv"))
    unit = UnitMapping("Unit 1", snapshot.sections, (), activity_target=101)
    result = calculate(snapshot, snapshot.students[0], unit, "Activities", Decimal(100))
    assert result.points == Decimal("51.04")
    assert "Only Participation" in result.breakdown[0]


def test_unit_without_any_activity_points_is_rejected(tmp_path):
    snapshot = read_snapshot(write_csv(tmp_path / "Example_report_1.csv"))
    unit = UnitMapping("Lab-only unit", ("2.12", "2.13"), (), activity_target=101)
    with pytest.raises(ImportProblem, match="no participation or challenge points"):
        calculate(snapshot, snapshot.students[0], unit, "Activities", Decimal(100))


def test_best_five_weights_labs_equally_despite_different_maxima(tmp_path):
    snapshot = read_snapshot(write_csv(tmp_path / "Example_report_1.csv"))
    unit = UnitMapping(
        "Unit 1",
        snapshot.sections,
        tuple(f"2.{i}" for i in range(12, 19)),
        lab_target=1,
        distinct_labs_confirmed=True,
    )
    result = calculate(snapshot, snapshot.students[0], unit, "Lab", Decimal(50))
    assert result.points == Decimal("40.00")
    assert sum(line.startswith("Counted") for line in result.breakdown) == 5


def write_shared_csv(path):
    write_csv(path)
    with path.open(newline="", encoding="utf-8") as stream:
        records = list(csv.reader(stream))
    records[1][3] = "Class A"
    other_student = records[1].copy()
    other_student[:4] = ["Other", "Student", "other@example.invalid", "Class B"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows([*records, other_student])
    return path


class FakeCanvas:
    def __init__(self):
        self.roster = [{"id": 1, "name": "Student Example", "email": "EXAMPLE@example.invalid"}]
        self.assignments = [
            {"id": 101, "name": "Unit 1 Activities — Chapter 2", "points_possible": 50},
            {"id": 102, "name": "Unit 1 Labs — Chapter 2", "points_possible": 50},
        ]
        self.records = [
            {
                "assignment_id": target,
                "user_id": 1,
                "score": None,
                "excused": False,
                "workflow_state": "unsubmitted",
            }
            for target in (101, 102)
        ]
        self.writes = []
        self.fail_read = False
        self.fail_targets = set()

    def get_course_users(self, course_id):
        return deepcopy(self.roster)

    def get_assignments(self, course_id):
        return deepcopy(self.assignments)

    def get_all_student_submissions(self, course_id):
        if self.fail_read:
            raise RuntimeError("private response")
        return deepcopy(self.records)

    def get_student_submissions(self, course_id, user_id):
        return [
            row for row in self.get_all_student_submissions(course_id) if row["user_id"] == user_id
        ]

    def get_submission(self, course_id, assignment_id, user_id):
        return next(
            deepcopy(row)
            for row in self.records
            if row["assignment_id"] == assignment_id and row["user_id"] == user_id
        )

    def submit_grade(self, course_id, assignment_id, user_id, score, comment=None):
        self.writes.append((course_id, assignment_id, user_id, score))
        if assignment_id in self.fail_targets:
            return False
        row = next(
            row
            for row in self.records
            if row["assignment_id"] == assignment_id and row["user_id"] == user_id
        )
        row.update(score=score, workflow_state="graded", grade=str(score))
        return True


@pytest.fixture
def setup_import(tmp_path):
    snapshot = read_snapshot(write_csv(tmp_path / "Example_report_1.csv"))
    canvas = FakeCanvas()
    coordinator = CsvImportCoordinator(
        canvas, 42, snapshot, MappingCache(tmp_path / "cache"), tmp_path / "workspace"
    )
    plan, reused = coordinator.load_mappings("Example")
    assert not reused
    plan = replace(
        plan, units=tuple(replace(unit, distinct_labs_confirmed=True) for unit in plan.units)
    )
    plan = coordinator.approve_mappings(plan, 42, "Example")
    return coordinator, canvas, plan


@pytest.mark.parametrize(
    "labs,expected",
    [
        ((100, 90, 80, 70, 60, 50, 40), "80"),
        ((100, 80, 60, 0, 0, 0, 0), "48"),
        ((100, 90, 80, 70, 60), "80"),
        ((0, 0, 0, 0, 0), "0"),
    ],
)
def test_best_five_reported_scores_and_explicit_zero_slots(tmp_path, labs, expected):
    snapshot = read_snapshot(write_csv(tmp_path / "scores.csv", labs))
    unit = UnitMapping(
        "Unit",
        snapshot.sections,
        tuple(section for section in snapshot.sections if section != "2.1"),
        lab_target=102,
        distinct_labs_confirmed=True,
    )
    assert calculate(snapshot, snapshot.students[0], unit, "Lab", Decimal(100)).points == Decimal(
        expected
    )


def test_ties_follow_stable_numeric_section_order(tmp_path):
    snapshot = read_snapshot(write_csv(tmp_path / "scores.csv", (80,) * 7))
    unit = UnitMapping(
        "Unit",
        snapshot.sections,
        tuple(reversed(snapshot.sections[1:])),
        lab_target=102,
        distinct_labs_confirmed=True,
    )
    result = calculate(snapshot, snapshot.students[0], unit, "Lab", Decimal(3))
    assert result.breakdown[0].startswith("Counted 2.12:")
    assert result.points == Decimal("2.40")


def test_activities_weight_sections_and_round_half_up(setup_import):
    coordinator, _, plan = setup_import
    snapshot = coordinator.snapshot
    result = calculate(snapshot, snapshot.students[0], plan.units[0], "Activities", Decimal(50))
    assert result.points == Decimal("35.00")
    student = replace(
        snapshot.students[0],
        scores=tuple(
            (key, Decimal("80.01") if score is not None else None)
            for key, score in snapshot.students[0].scores
        ),
    )
    assert calculate(snapshot, student, plan.units[0], "Activities", Decimal(50)).points == Decimal(
        "40.01"
    )


def test_selected_subset_uses_columns_not_report_totals(setup_import):
    coordinator, _, plan = setup_import
    unit = replace(plan.units[0], sections=("2.1",))
    assert calculate(
        coordinator.snapshot, coordinator.snapshot.students[0], unit, "Activities", Decimal(50)
    ).points == Decimal(35)


@pytest.mark.parametrize("maximum,expected", [(50, "35.00"), (100, "70.00"), (75, "52.50")])
def test_activities_scale_to_canvas_assignment_points(setup_import, maximum, expected):
    coordinator, _, plan = setup_import
    result = calculate(
        coordinator.snapshot,
        coordinator.snapshot.students[0],
        plan.units[0],
        "Activities",
        Decimal(maximum),
    )
    assert result.points == Decimal(expected)
    assert all(f"× {Decimal(maximum) / 2} =" in line for line in result.breakdown)


@pytest.mark.parametrize("maximum", ["0", "-1", "NaN", "Infinity"])
def test_invalid_canvas_maximum_is_blocked(setup_import, maximum):
    coordinator, _, plan = setup_import
    with pytest.raises(ImportProblem, match="positive possible points"):
        calculate(
            coordinator.snapshot,
            coordinator.snapshot.students[0],
            plan.units[0],
            "Activities",
            Decimal(maximum),
        )


@pytest.mark.parametrize("change", ["small_pool", "missing_lab", "unconfirmed", "empty_category"])
def test_unsupported_pools_or_targets_block_scoring(setup_import, change):
    coordinator, _, plan = setup_import
    unit = plan.units[0]
    kind, maximum = "Lab", Decimal(50)
    if change == "small_pool":
        unit = replace(unit, lab_sections=unit.lab_sections[:4])
    elif change == "missing_lab":
        unit = replace(unit, lab_sections=unit.lab_sections[:-1] + ("2.99",))
    elif change == "unconfirmed":
        unit = replace(unit, distinct_labs_confirmed=False)
    else:
        kind = "Activities"
        unit = replace(unit, sections=("2.12",))
    with pytest.raises(ImportProblem):
        calculate(coordinator.snapshot, coordinator.snapshot.students[0], unit, kind, maximum)


@pytest.mark.parametrize("bad", ["", "N/A", "bad", "NaN", "Infinity", "-1", "101"])
def test_invalid_cells_are_not_unattempted(tmp_path, bad):
    with pytest.raises(ImportProblem):
        read_snapshot(write_csv(tmp_path / "bad.csv", (bad, 80, 60, 0, 0)))


def test_duplicate_source_identity_or_heading_is_rejected(tmp_path):
    path = write_csv(tmp_path / "scores.csv")
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines + [lines[1]]))
    with pytest.raises(ImportProblem, match="duplicate source"):
        read_snapshot(path)
    path.write_text(lines[0].replace("First name", "Last name") + "\n" + lines[1])
    with pytest.raises(ImportProblem, match="duplicate headings"):
        read_snapshot(path)


def test_range_parser_and_no_chapter_unit_assumption(setup_import):
    coordinator, _, _ = setup_import
    assert parse_sections("2.1-2.3,2.12") == ("2.1", "2.2", "2.3", "2.12")
    assert (
        propose_units(
            coordinator.snapshot, [{"id": 1, "name": "Unit 2 Lab", "points_possible": 50}]
        )
        == ()
    )
    with pytest.raises(ImportProblem):
        parse_sections("2.1-3.4")


def test_ambiguous_email_is_not_automatically_assigned(setup_import):
    coordinator, canvas, _ = setup_import
    roster = canvas.roster + [{**canvas.roster[0], "id": 2}]
    assert propose_students(coordinator.snapshot, roster)[0].canvas_id is None


NEW_LAYOUT_IDENTITY = ["Last name", "First name", "Primary email", "School email", "Student ID"]


def write_new_layout_csv(path, identity_headers=None, school_email="", student_id="0012345"):
    """Synthetic export using the School email / Student ID identity layout."""
    headers = identity_headers or NEW_LAYOUT_IDENTITY
    score_headers = [
        "Total (10)",
        "Participation total (10)",
        "Challenge total (0)",
        "Lab total (0)",
        "1.1 - Participation (10)",
        "1.1 - Challenge (0)",
        "1.1 - Lab (0)",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers + score_headers)
        writer.writerow(
            ["Example", "Student", "primary@example.invalid", school_email, student_id]
            + [5, 5, 0, 0, 5, "N/A", "N/A"]
        )
    return path


def test_new_identity_layout_parses_with_school_email_and_student_id(tmp_path):
    path = write_new_layout_csv(tmp_path / "X_report_1.csv", school_email="School@Example.invalid")
    student = read_snapshot(path).students[0]
    assert student.email == "primary@example.invalid"
    assert student.school_email == "school@example.invalid"
    assert student.student_id == "0012345"
    assert student.class_section == ""
    assert dict(student.scores)["1.1|Participation"] == Decimal(5)


def test_blank_school_email_and_student_id_are_allowed(tmp_path):
    path = write_new_layout_csv(tmp_path / "X_report_1.csv", student_id="")
    student = read_snapshot(path).students[0]
    assert (student.school_email, student.student_id) == ("", "")


def test_unknown_identity_layout_is_rejected(tmp_path):
    path = write_new_layout_csv(
        tmp_path / "X_report_1.csv",
        identity_headers=["Last name", "First name", "Primary email", "Student ID", "School email"],
    )
    with pytest.raises(ImportProblem, match="Unsupported identity headers"):
        read_snapshot(path)


def test_students_match_on_school_email_or_sis_user_id(tmp_path):
    path = write_new_layout_csv(tmp_path / "X_report_1.csv", school_email="School@Example.invalid")
    snapshot = read_snapshot(path)
    by_school_email = [{"id": 7, "name": "Other Name", "email": "school@example.invalid"}]
    by_sis_id = [{"id": 8, "name": "Other Name", "sis_user_id": " 0012345 "}]
    unrelated = [{"id": 9, "name": "Other Name", "sis_user_id": "999"}]
    assert propose_students(snapshot, by_school_email)[0].canvas_id == 7
    assert propose_students(snapshot, by_school_email)[0].method == "Email"
    assert propose_students(snapshot, by_sis_id)[0].canvas_id == 8
    assert propose_students(snapshot, unrelated)[0].canvas_id is None


def ai_mapping_response(sections):
    return {
        "mappings": [
            {
                "assignment_id": 101,
                "category": "Activities",
                "sections": list(sections),
                "reason": "Activities description specifies Chapter 2.",
            },
            {
                "assignment_id": 102,
                "category": "Lab",
                "sections": list(sections),
                "reason": "Lab description specifies Chapter 2.",
            },
        ],
        "unresolved": "",
    }


def test_assumption_based_mappings_are_flagged_for_verification(setup_import):
    coordinator, _, plan = setup_import
    response = ai_mapping_response(coordinator.snapshot.sections)
    response["mappings"][0]["reason"] = "Assumption: the CSV covers one chapter matching U01."
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(content=json.dumps(response))
    proposed, notes = coordinator.suggest_mappings(plan, llm, refresh=True)
    assert notes.splitlines()[0].startswith("Some mappings are assumptions")
    assert not proposed.approved


def test_single_chapter_alignment_is_allowed_but_must_be_labelled(setup_import):
    coordinator, _, plan = setup_import
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content=json.dumps(ai_mapping_response(coordinator.snapshot.sections))
    )
    coordinator.suggest_mappings(plan, llm, refresh=True)
    system_prompt = llm.generate_sync.call_args.kwargs["system_prompt"]
    assert "exactly one chapter" in system_prompt
    assert "start with 'Assumption:'" in system_prompt


def test_ai_mapping_uses_metadata_only_and_exact_canvas_names(setup_import):
    coordinator, canvas, plan = setup_import
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content=json.dumps(ai_mapping_response(coordinator.snapshot.sections))
    )
    proposed, notes = coordinator.suggest_mappings(plan, llm)
    assert [unit.name for unit in proposed.units] == [row["name"] for row in canvas.assignments]
    assert not proposed.approved
    assert not any(unit.distinct_labs_confirmed for unit in proposed.units)
    assert proposed.students == plan.students
    assert "Chapter 2" in notes
    request = llm.generate_sync.call_args.kwargs
    payload = json.loads(request["prompt"])
    assert set(payload) == {"columns", "assignments"}
    assert all(
        set(row) == {"id", "name", "description", "points_possible"}
        for row in payload["assignments"]
    )
    for student in coordinator.snapshot.students:
        assert student.email not in request["prompt"]
        assert student.name not in request["prompt"]
    assert (
        coordinator.cache.load(
            coordinator.snapshot, 42, "Example", canvas.roster, canvas.assignments
        )
        == plan
    )
    assert not canvas.writes


@pytest.mark.parametrize(
    "defect", ["json", "id", "section", "duplicate", "category", "lab_pool", "type"]
)
def test_invalid_ai_proposals_do_not_replace_approved_mappings(setup_import, defect):
    coordinator, canvas, plan = setup_import
    payload = ai_mapping_response(coordinator.snapshot.sections)
    if defect == "id":
        payload["mappings"][0]["assignment_id"] = 999
    elif defect == "section":
        payload["mappings"][0]["sections"] = ["9.99"]
    elif defect == "duplicate":
        payload["mappings"].append(payload["mappings"][0].copy())
    elif defect == "category":
        payload["mappings"][0]["category"] = "Quiz"
    elif defect == "lab_pool":
        payload["mappings"][1]["sections"] = ["2.12"]
    elif defect == "type":
        payload["mappings"][0]["assignment_id"] = True
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content="not JSON" if defect == "json" else json.dumps(payload)
    )
    with pytest.raises(ImportProblem):
        coordinator.suggest_mappings(plan, llm)
    assert (
        coordinator.cache.load(
            coordinator.snapshot, 42, "Example", canvas.roster, canvas.assignments
        )
        == plan
    )
    assert not canvas.writes


@pytest.mark.parametrize("language", ["json", "JSON", ""])
def test_ai_mapping_accepts_a_complete_json_code_block(setup_import, language):
    coordinator, canvas, plan = setup_import
    payload = json.dumps(ai_mapping_response(coordinator.snapshot.sections))
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(content=f" \n```{language}\n{payload}\n```\n ")
    proposed, notes = coordinator.suggest_mappings(plan, llm)
    assert [unit.name for unit in proposed.units] == [row["name"] for row in canvas.assignments]
    assert not proposed.approved
    assert not any(unit.distinct_labs_confirmed for unit in proposed.units)
    assert proposed.students == plan.students
    assert "Chapter 2" in notes
    assert not canvas.writes


@pytest.mark.parametrize("defect", ["incomplete", "prose", "multiple", "invalid-id", "type"])
def test_fenced_ai_responses_still_require_valid_complete_mappings(setup_import, defect):
    coordinator, canvas, plan = setup_import
    payload = ai_mapping_response(coordinator.snapshot.sections)
    if defect == "invalid-id":
        payload["mappings"][0]["assignment_id"] = 999
    if defect == "type":
        payload["mappings"][0]["assignment_id"] = True
    content = f"```json\n{json.dumps(payload)}\n```"
    if defect == "incomplete":
        content = content[:-3]
    elif defect == "prose":
        content = "Here are your mappings:\n" + content
    elif defect == "multiple":
        content += "\n" + content
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(content=content)
    with pytest.raises(ImportProblem):
        coordinator.suggest_mappings(plan, llm)
    assert (
        coordinator.cache.load(
            coordinator.snapshot, 42, "Example", canvas.roster, canvas.assignments
        )
        == plan
    )
    assert not canvas.writes


def test_ai_unresolved_or_service_failure_keeps_existing_mappings(setup_import):
    coordinator, canvas, plan = setup_import
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content=json.dumps(
            {
                "mappings": [],
                "unresolved": "Assignment titles alone do not identify chapter coverage.",
            }
        )
    )
    proposed, notes = coordinator.suggest_mappings(plan, llm)
    assert proposed == plan
    assert "coverage" in notes
    llm.generate_sync.side_effect = RuntimeError("private provider response")
    with pytest.raises(ImportProblem, match="request failed"):
        coordinator.suggest_mappings(plan, llm, refresh=True)
    assert not canvas.writes


def test_ai_drafts_persist_without_approval_and_refresh_explicitly(setup_import):
    coordinator, canvas, plan = setup_import
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content=json.dumps(ai_mapping_response(coordinator.snapshot.sections))
    )
    proposed, _ = coordinator.suggest_mappings(plan, llm)
    restarted = CsvImportCoordinator(
        canvas, 42, coordinator.snapshot, coordinator.cache, coordinator.workspace_directory
    )
    restarted.roster = canvas.roster
    restarted.assignments = canvas.assignments
    reused, notes = restarted.suggest_mappings(plan, None)
    assert reused == proposed and not reused.approved
    assert "Cached AI proposal" in notes
    assert not any(unit.distinct_labs_confirmed for unit in reused.units)
    assert llm.generate_sync.call_count == 1
    restarted.suggest_mappings(plan, llm, refresh=True)
    assert llm.generate_sync.call_count == 2
    for path in coordinator.cache.root.glob("ai-*.json"):
        content = path.read_text(encoding="utf-8")
        assert "example@example.invalid" not in content
        assert "Student Example" not in content


@pytest.mark.parametrize("change", ["book", "course", "columns", "assignment"])
def test_ai_proposal_cache_invalidates_metadata_changes(setup_import, change):
    coordinator, _, plan = setup_import
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content=json.dumps({"mappings": [], "unresolved": "No supported match."})
    )
    coordinator.suggest_mappings(plan, llm)
    if change == "book":
        plan = replace(plan, book_id="Other")
    elif change == "course":
        coordinator.course_id = 99
    elif change == "columns":
        coordinator.snapshot = replace(
            coordinator.snapshot, columns=coordinator.snapshot.columns[:-1]
        )
    else:
        coordinator.assignments[0]["description"] = "New chapter scope"
    coordinator.suggest_mappings(plan, llm)
    assert llm.generate_sync.call_count == 2


@pytest.mark.parametrize("title", ["U01T01: Quiz", "Unit 1 Discussion", "Participation Quiz"])
def test_ai_cannot_map_scores_to_unrelated_gradebook_targets(setup_import, title):
    coordinator, _, plan = setup_import
    coordinator.assignments[0]["name"] = title
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content=json.dumps(ai_mapping_response(coordinator.snapshot.sections))
    )
    with pytest.raises(ImportProblem, match="assignment kind"):
        coordinator.suggest_mappings(plan, llm)


def test_named_participation_challenge_and_lab_targets_share_explicit_unit(setup_import):
    coordinator, _, _ = setup_import
    assignments = [
        {
            "id": 101,
            "name": "U01A01: zyBook Participation & Challenge",
            "description": "<p>Chapter 2</p>",
            "points_possible": 100,
        },
        {
            "id": 102,
            "name": "U01A02: zyLabs",
            "description": "<p>Chapter 2</p>",
            "points_possible": 50,
        },
    ]
    units = propose_units(coordinator.snapshot, assignments)
    assert len(units) == 1
    assert units[0].name == "U01"
    assert units[0].activity_target == 101 and units[0].lab_target == 102
    assert units[0].sections == coordinator.snapshot.sections
    assert (
        propose_units(
            coordinator.snapshot, [{**assignment, "description": ""} for assignment in assignments]
        )
        == ()
    )


def test_shared_book_csv_keeps_course_mappings_and_posts_separate(tmp_path):
    snapshot = read_snapshot(write_shared_csv(tmp_path / "SharedBook_report_1.csv"))
    cache = MappingCache(tmp_path / "cache")
    approved_plans = []
    for course_id, student in enumerate(snapshot.students, 42):
        canvas = FakeCanvas()
        student_id = course_id + 100
        canvas.roster = [{"id": student_id, "name": student.name, "email": student.email}]
        canvas.assignments[0]["points_possible"] = 100 if course_id == 42 else 50
        for record in canvas.records:
            record["user_id"] = student_id
        coordinator = CsvImportCoordinator(
            canvas, course_id, snapshot, cache, tmp_path / "workspace"
        )
        plan, reused = coordinator.load_mappings("SharedBook")
        assert not reused
        plan = replace(
            plan, units=tuple(replace(unit, distinct_labs_confirmed=True) for unit in plan.units)
        )
        plan = coordinator.approve_mappings(plan, course_id, "SharedBook")
        preview = coordinator.prepare_preview(plan, {101})
        assert preview.unmatched_source == 1
        assert preview.unmatched_canvas == 0
        assert len(preview.rows) == 1
        assert preview.rows[0].student_id == student_id
        expected = Decimal("70.00") if course_id == 42 else Decimal("35.00")
        assert preview.rows[0].new_points == expected
        assert not canvas.writes
        approval = coordinator.approve_rows(preview, {preview.rows[0].key})
        results = coordinator.submit(plan, {101}, False, approval, execute=True)
        assert results[0].status == "Verified"
        assert canvas.writes == [(course_id, 101, student_id, float(expected))]
        approved_plans.append((coordinator, plan))
    assert cache.path(42, "SharedBook") != cache.path(43, "SharedBook")
    for coordinator, plan in approved_plans:
        assert coordinator.load_mappings("SharedBook") == (plan, True)


def test_cache_reuse_scope_and_invalidation(setup_import):
    coordinator, canvas, plan = setup_import
    loaded, cached = coordinator.load_mappings("Example")
    assert cached and loaded == plan
    assert (
        coordinator.cache.load(
            coordinator.snapshot, 99, "Example", canvas.roster, canvas.assignments
        )
        is None
    )
    assert (
        coordinator.cache.load(
            coordinator.snapshot, 42, "OtherBook", canvas.roster, canvas.assignments
        )
        is None
    )
    canvas.roster[0]["email"] = "changed@example.invalid"
    _, cached = coordinator.load_mappings("Example")
    assert not cached


def test_mapping_correction_is_saved_only_after_approval(setup_import):
    coordinator, _, plan = setup_import
    draft = replace(
        plan, approved=False, students=(replace(plan.students[0], canvas_id=None, method="Manual"),)
    )
    with pytest.raises(ImportProblem):
        coordinator.cache.save(draft)
    saved = coordinator.approve_mappings(draft, 42, "Example")
    assert coordinator.load_mappings("Example") == (saved, True)
    with pytest.raises(ImportProblem, match="pairing"):
        coordinator.approve_mappings(draft, 99, "Example")


def test_cache_version_or_schema_change_requires_review(setup_import):
    coordinator, canvas, plan = setup_import
    path = coordinator.cache.path(42, "Example")
    payload = json.loads(path.read_text())
    payload["version"] = 999
    path.write_text(json.dumps(payload))
    assert (
        coordinator.cache.load(
            coordinator.snapshot, 42, "Example", canvas.roster, canvas.assignments
        )
        is None
    )
    coordinator.cache.save(plan)
    snapshot = replace(coordinator.snapshot, schema_hash="changed")
    assert (
        coordinator.cache.load(snapshot, 42, "Example", canvas.roster, canvas.assignments) is None
    )


def test_conflicting_targets_or_memberships_are_blocked(setup_import):
    coordinator, _, plan = setup_import
    unit = plan.units[0]
    with pytest.raises(ImportProblem, match="conflict"):
        coordinator.approve_mappings(
            replace(plan, units=(unit, replace(unit, name="Another"))), 42, "Example"
        )


def test_no_writes_before_approval_and_ungraded_default(setup_import):
    coordinator, canvas, plan = setup_import
    # A Canvas score above the calculated one is a decrease: skipped by default.
    canvas.records[0]["score"] = 50
    preview = coordinator.prepare_preview(plan, {101, 102})
    assert any(row.status == "Skipped: already graded" for row in preview.rows)
    assert not canvas.writes
    with pytest.raises(ImportProblem):
        coordinator.submit(plan, {101, 102}, False, None)
    assert not canvas.writes


def test_placeholder_zero_is_raised_without_include_existing(setup_import):
    """A late joiner zeroed by "Zero Ungraded" must not stay at zero forever."""
    coordinator, canvas, plan = setup_import
    for record in canvas.records:
        record["score"] = 0.0
    preview = coordinator.prepare_preview(plan, {101, 102})
    raised = [row for row in preview.rows if row.new_points > 0]
    assert raised and all(row.status == "Ready" for row in raised)
    assert all(not row.risky for row in raised)


def test_unmatched_canvas_students_stay_out_of_rows(setup_import):
    coordinator, canvas, _ = setup_import
    canvas.roster.append({"id": 2, "name": "Missing Student", "email": "missing@example.invalid"})
    plan, _ = coordinator.load_mappings("Example")
    plan = replace(
        plan, units=tuple(replace(unit, distinct_labs_confirmed=True) for unit in plan.units)
    )
    plan = coordinator.approve_mappings(plan, 42, "Example")
    preview = coordinator.prepare_preview(plan, {102})
    assert preview.unmatched_canvas == 1 and len(preview.rows) == 1


def test_excused_and_unchanged_are_skipped(setup_import):
    coordinator, canvas, plan = setup_import
    canvas.records[0]["excused"] = True
    canvas.records[1]["score"] = 40
    preview = coordinator.prepare_preview(plan, {101, 102}, True)
    assert {row.status for row in preview.rows} == {"Skipped: excused", "Skipped: unchanged"}


def test_read_failure_or_missing_excused_state_blocks_preview(setup_import):
    coordinator, canvas, plan = setup_import
    canvas.fail_read = True
    with pytest.raises(ImportProblem, match="Canvas reported"):
        coordinator.prepare_preview(plan, {102})
    canvas.fail_read = False
    del canvas.records[0]["excused"]
    with pytest.raises(ImportProblem, match="score/excused state"):
        coordinator.prepare_preview(plan, {102})
    assert not canvas.writes


def test_null_excused_is_treated_as_not_excused(setup_import):
    coordinator, canvas, plan = setup_import
    for record in canvas.records:
        record["excused"] = None
    preview = coordinator.prepare_preview(plan, {101, 102})
    assert len(preview.rows) == 2
    assert not any(row.status.startswith("Skipped") for row in preview.rows)


def test_non_boolean_excused_still_blocks_preview(setup_import):
    coordinator, canvas, plan = setup_import
    canvas.records[0]["excused"] = "false"
    with pytest.raises(ImportProblem, match="score/excused state"):
        coordinator.prepare_preview(plan, {101, 102})
    assert not canvas.writes


@pytest.mark.parametrize(
    "sections,expected",
    [
        (("2.1",), "2.1"),
        (("2.12", "2.13", "2.14"), "2.12-2.14"),
        (("2.1", "2.3", "2.4", "3.1"), "2.1, 2.3-2.4, 3.1"),
        ((), ""),
    ],
)
def test_format_sections_compresses_consecutive_ranges(sections, expected):
    assert format_sections(sections) == expected


def test_decreases_need_separate_approval_and_are_read_back(setup_import):
    coordinator, canvas, plan = setup_import
    canvas.records[1]["score"] = 50
    preview = coordinator.prepare_preview(plan, {102}, True)
    keys = {row.key for row in preview.rows}
    with pytest.raises(ImportProblem, match="zeros/decreases"):
        coordinator.approve_rows(preview, keys)
    approved = coordinator.approve_rows(preview, keys, True)
    results = coordinator.submit(plan, {102}, True, approved, execute=True)
    assert [result.status for result in results] == ["Verified"]
    assert len(canvas.writes) == 1
    with pytest.raises(ImportProblem):
        coordinator.submit(plan, {102}, True, approved, execute=True)
    assert len(canvas.writes) == 1


@pytest.mark.parametrize("change", ["file", "mapping", "selection", "existing", "canvas", "roster"])
def test_changed_preview_inputs_invalidate_approval(setup_import, change):
    coordinator, canvas, plan = setup_import
    preview = coordinator.prepare_preview(plan, {101, 102})
    approved = coordinator.approve_rows(preview, {row.key for row in preview.rows})
    targets, existing = {101, 102}, False
    if change == "file":
        path = coordinator.snapshot.path
        path.write_bytes(path.read_bytes() + b"\n")
    elif change == "mapping":
        plan = replace(plan, book_id="different")
    elif change == "selection":
        targets = {102}
    elif change == "existing":
        existing = True
    elif change == "canvas":
        canvas.records[1]["score"] = 1
    else:
        canvas.roster[0]["name"] = "Changed"
    with pytest.raises(ImportProblem):
        coordinator.submit(plan, targets, existing, approved, execute=True)
    assert not canvas.writes


def test_partial_failure_has_per_row_results_and_no_replay(setup_import):
    coordinator, canvas, plan = setup_import
    canvas.fail_targets.add(102)
    preview = coordinator.prepare_preview(plan, {101, 102})
    approval = coordinator.approve_rows(preview, {row.key for row in preview.rows})
    results = coordinator.submit(plan, {101, 102}, False, approval, execute=True)
    assert {result.status for result in results} == {"Verified", "Failed: no retry"}
    assert len(canvas.writes) == 2


def test_actual_weighted_sections_and_subset_have_different_totals(setup_import):
    from coursewright.ta.zybooks.importer import ScoreColumn

    coordinator, _, plan = setup_import
    snapshot = coordinator.snapshot
    student = snapshot.students[0]
    columns = snapshot.columns + (
        ScoreColumn("2.2", "Participation", Decimal(30)),
        ScoreColumn("2.2", "Challenge", Decimal(15)),
    )
    student = replace(
        student,
        scores=student.scores
        + (("2.2|Participation", Decimal(60)), ("2.2|Challenge", Decimal(100))),
    )
    snapshot = replace(snapshot, columns=columns, students=(student,))
    combined = replace(plan.units[0], sections=("2.1", "2.2"))
    subset = replace(combined, sections=("2.1",))
    assert calculate(snapshot, student, combined, "Activities", Decimal(50)).points == Decimal(
        "38.75"
    )
    assert calculate(snapshot, student, subset, "Activities", Decimal(50)).points == Decimal(
        "35.00"
    )


def test_missing_canvas_record_blocks_posting_not_zero(setup_import):
    coordinator, canvas, plan = setup_import
    canvas.records.pop()
    preview = coordinator.prepare_preview(plan, {101, 102})
    assert any(row.status.startswith("Blocked") for row in preview.rows)
    with pytest.raises(ImportProblem, match="missing Canvas"):
        coordinator.approve_rows(
            preview, {row.key for row in preview.rows if row.status == "Ready"}
        )
    assert not canvas.writes


def test_zero_rows_require_explicit_approval(setup_import):
    coordinator, canvas, plan = setup_import
    snapshot = coordinator.snapshot
    student = replace(
        snapshot.students[0],
        scores=tuple(
            (key, Decimal(0) if score is not None else None)
            for key, score in snapshot.students[0].scores
        ),
    )
    coordinator.snapshot = replace(snapshot, students=(student,))
    preview = coordinator.prepare_preview(plan, {102})
    with pytest.raises(ImportProblem, match="zeros/decreases"):
        coordinator.approve_rows(preview, {row.key for row in preview.rows})
    coordinator.approve_rows(preview, {row.key for row in preview.rows}, True)
    assert not canvas.writes


def test_change_between_rows_stops_remaining_writes(setup_import, monkeypatch):
    coordinator, canvas, plan = setup_import
    preview = coordinator.prepare_preview(plan, {101, 102})
    approval = coordinator.approve_rows(preview, {row.key for row in preview.rows})
    original = canvas.submit_grade

    def post_then_change_source(*args):
        result = original(*args)
        path = coordinator.snapshot.path
        path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(canvas, "submit_grade", post_then_change_source)
    results = coordinator.submit(plan, {101, 102}, False, approval, execute=True)
    assert len(canvas.writes) == 1
    assert results[0].status == "Verified"
    assert results[1].status.startswith("Not sent: source changed")


def test_csv_import_modules_do_not_require_browser_or_retrieval_imports():
    script = """
import importlib.abc
import sys
# Simulate an installation without the optional browser extra. The existing
# application bootstrap tolerates its absence; these modules must do so too.
sys.modules['playwright'] = None
sys.modules['playwright.sync_api'] = None
class DenyBrowser(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {
            'coursewright.ta.zybooks.session', 'coursewright.ta.zybooks.client',
            'coursewright.ta.zybooks.parser',
        }:
            raise AssertionError('File import must not require browser modules')
sys.meta_path.insert(0, DenyBrowser())
import coursewright.ta.zybooks.importer
# Library code is silent: importing it must not drag in a terminal library
# or anything from ui/ (AGENTS.md 3.6, 3.8).
loaded = [
    name for name in sys.modules
    if name.split('.')[0] in {'rich', 'typer', 'textual'}
    or name == 'coursewright.ui' or name.startswith('coursewright.ui.')
]
assert not loaded, loaded
"""
    # Point the child at the same source tree this test imported from.
    source_root = str(Path(importer.__file__).parents[3])
    result = subprocess.run(
        [sys.executable, "-"],
        input=script,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTHONPATH": source_root},
    )
    assert result.returncode == 0, result.stderr


def test_discovery_is_core_inbox_and_the_local_copy_is_gone():
    """File discovery was deleted here; core/inbox is the only implementation."""
    assert not hasattr(importer, "detect_csv")
    assert not hasattr(importer, "default_download_directory")
    assert not hasattr(importer, "WINDOWS_DOWNLOADS_FOLDER_GUID")


def test_score_report_discovery_uses_downloads_first_and_ignores_strangers(tmp_path):
    """Downloads holds bank exports etc.; only zyBooks report names qualify."""
    downloads = tmp_path / "Downloads"
    workspace_import = tmp_path / "workspace" / "import"
    downloads.mkdir()
    workspace_import.mkdir(parents=True)
    (downloads / "bank_statement.csv").write_text("a,b\n", encoding="utf-8")
    fallback = workspace_import / "ExampleBook_report_1_2026-10-24.csv"
    fallback.write_text("a,b\n", encoding="utf-8")
    kwargs = {"download_directory": downloads, "workspace_import_directory": workspace_import}

    # Nothing in Downloads matches, so the workspace folder is the fallback.
    candidate = importer.find_score_report_candidate(**kwargs)
    assert candidate is not None
    assert candidate.path == fallback
    assert candidate.found_in == CandidateLocation.WORKSPACE_IMPORT

    # A matching file in Downloads wins over the workspace fallback.
    report = downloads / "ExampleBook_report_2_2026-10-25.csv"
    report.write_text("a,b\n", encoding="utf-8")
    candidate = importer.find_score_report_candidate(**kwargs)
    assert candidate is not None
    assert candidate.path == report
    assert candidate.found_in == CandidateLocation.DOWNLOADS


def test_score_report_discovery_finds_nothing_in_empty_folders(tmp_path):
    assert (
        importer.find_score_report_candidate(
            download_directory=tmp_path / "missing",
            workspace_import_directory=tmp_path / "also-missing",
        )
        is None
    )


# ----- the LLM privacy boundary ------------------------------------------------

SENTINEL_NAME = "Zelda Quimby"
SENTINEL_EMAIL = "zq-sentinel@example.invalid"
SENTINEL_SCHOOL_EMAIL = "zq-school@example.invalid"
SENTINEL_STUDENT_ID = "SENTINEL-77"
SENTINEL_SCORES = (Decimal("91.37"), Decimal("63.21"), Decimal("88.88"))


def sentinel_snapshot(snapshot):
    """Return the snapshot with one student whose every field is recognisable."""
    scored = iter(SENTINEL_SCORES * 20)
    student = replace(
        snapshot.students[0],
        email=SENTINEL_EMAIL,
        name=SENTINEL_NAME,
        school_email=SENTINEL_SCHOOL_EMAIL,
        student_id=SENTINEL_STUDENT_ID,
        class_section="Sentinel Section",
        scores=tuple(
            (key, next(scored) if score is not None else None)
            for key, score in snapshot.students[0].scores
        ),
    )
    return replace(snapshot, students=(student,))


def test_llm_request_payload_contains_no_student_name_email_or_score(setup_import):
    """Acceptance 5: only assignment and column metadata go to the model."""
    coordinator, canvas, _ = setup_import
    snapshot = sentinel_snapshot(coordinator.snapshot)
    # Canvas roster names and emails are student data too.
    canvas.roster[0]["name"] = SENTINEL_NAME
    canvas.roster[0]["email"] = SENTINEL_EMAIL
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content=json.dumps(ai_mapping_response(snapshot.sections))
    )
    importer.suggest_units_with_llm(snapshot, canvas.get_assignments(42), llm)

    request = llm.generate_sync.call_args.kwargs
    everything_sent = json.dumps(request, default=str)
    forbidden = [
        SENTINEL_NAME,
        "Zelda",
        "Quimby",
        SENTINEL_EMAIL,
        "zq-sentinel",
        SENTINEL_SCHOOL_EMAIL,
        SENTINEL_STUDENT_ID,
        "Sentinel Section",
        *(str(score) for score in SENTINEL_SCORES),
    ]
    for text in forbidden:
        assert text not in everything_sent, text
    # The prompt is exactly metadata: columns and assignments, nothing else.
    payload = json.loads(request["prompt"])
    assert set(payload) == {"columns", "assignments"}
    assert all(set(column) == {"section", "category", "possible"} for column in payload["columns"])
    # The payload must actually carry the assignment titles and the column
    # metadata, not just the right keys: a payload that dropped them would be
    # useless to the model, so the boundary is about content, not shape.
    titles_in_payload = {assignment["name"] for assignment in payload["assignments"]}
    assert "Unit 1 Activities — Chapter 2" in titles_in_payload
    assert "Unit 1 Labs — Chapter 2" in titles_in_payload
    assert {column["section"] for column in payload["columns"]} == set(snapshot.sections)
    # Point values round-trip as numbers; zero-point columns are legitimate.
    assert all(Decimal(str(column["possible"])) >= 0 for column in payload["columns"])


def test_llm_metadata_is_built_without_reading_student_rows(setup_import):
    """build_llm_metadata never touches snapshot.students, so none can leak."""
    coordinator, canvas, _ = setup_import
    snapshot = coordinator.snapshot
    expected = importer.build_llm_metadata(snapshot, canvas.get_assignments(42))
    without_students = importer.build_llm_metadata(
        replace(snapshot, students=()), canvas.get_assignments(42)
    )
    assert without_students == expected


def test_coordinator_suggestion_sends_no_student_data(setup_import):
    """The same boundary holds through the coordinator, cache and all."""
    coordinator, canvas, plan = setup_import
    coordinator.snapshot = sentinel_snapshot(coordinator.snapshot)
    llm = Mock()
    llm.generate_sync.return_value = SimpleNamespace(
        content=json.dumps(ai_mapping_response(coordinator.snapshot.sections))
    )
    # The mapping plan was approved for the original snapshot; a refreshed one
    # may fail validation, which is fine: the request was already made.
    with suppress(ImportProblem):
        coordinator.suggest_mappings(plan, llm, refresh=True)
    sent = json.dumps(llm.generate_sync.call_args.kwargs, default=str)
    assert SENTINEL_NAME not in sent
    assert SENTINEL_EMAIL not in sent
    assert all(str(score) not in sent for score in SENTINEL_SCORES)


# ----- grades are sent only through the safe-write path ------------------------


def approved_rows(coordinator, plan, targets=frozenset({101, 102})):
    preview = coordinator.prepare_preview(plan, set(targets))
    return coordinator.approve_rows(preview, {row.key for row in preview.rows})


def test_submit_is_a_dry_run_by_default(setup_import, tmp_path):
    coordinator, canvas, plan = setup_import
    approval = approved_rows(coordinator, plan)
    results = coordinator.submit(plan, {101, 102}, False, approval)
    assert {result.status for result in results} == {"Dry run: not sent"}
    assert canvas.writes == []
    assert not (tmp_path / "workspace" / "backups").exists()


def test_executed_grades_are_backed_up_in_the_workspace_before_each_write(setup_import, tmp_path):
    coordinator, canvas, plan = setup_import
    backup_directory = tmp_path / "workspace" / "backups" / "canvas"
    backups_seen_at_write = []
    original = canvas.submit_grade

    def record_backups_then_write(*args):
        backups_seen_at_write.append(len(list(backup_directory.glob("*_submit_grade.json"))))
        return original(*args)

    canvas.submit_grade = record_backups_then_write
    approval = approved_rows(coordinator, plan)
    results = coordinator.submit(plan, {101, 102}, False, approval, execute=True)
    assert {result.status for result in results} == {"Verified"}
    # One more backup existed at each successive write: backup first, then write.
    assert backups_seen_at_write == [1, 2]
    record = json.loads(sorted(backup_directory.glob("*_submit_grade.json"))[0].read_text())
    assert record["operation"] == "submit_grade"
    assert set(record["payload"]) == {"score"}
    assert record["state_before"]["workflow_state"] == "unsubmitted"


def test_no_workspace_means_nothing_is_sent(setup_import, monkeypatch):
    coordinator, canvas, plan = setup_import
    coordinator.workspace_directory = None
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.chdir(coordinator.snapshot.path.parent)
    approval = approved_rows(coordinator, plan)
    with pytest.raises(ImportProblem, match="workspace"):
        coordinator.submit(plan, {101, 102}, False, approval, execute=True)
    assert canvas.writes == []


def test_a_failed_backup_stops_the_batch_before_any_write(setup_import, tmp_path):
    coordinator, canvas, plan = setup_import
    # A file where the backup folder should be makes every backup fail.
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "backups").write_text("not a folder", encoding="utf-8")
    approval = approved_rows(coordinator, plan)
    results = coordinator.submit(plan, {101, 102}, False, approval, execute=True)
    assert canvas.writes == []
    assert results[0].status == "Not sent: backup failed"
    assert {result.status for result in results[1:]} == {"Not sent: approval invalidated"}


def test_importer_never_calls_the_client_to_write_a_grade():
    """Static guard: the only grade write in the module is the safe writer's."""
    source = Path(importer.__file__).read_text(encoding="utf-8")
    assert "client.submit_grade" not in source
    assert "submit_multiple_grades" not in source
    assert "writer.submit_grade" in source


# ----- private workspace for caches --------------------------------------------


def test_mapping_cache_defaults_to_the_private_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path / "workspace"))
    cache = MappingCache()
    assert cache.root == tmp_path / "workspace" / "zybooks-mappings"
    assert "exports" not in cache.root.parts


def test_mapping_cache_without_a_workspace_is_refused(monkeypatch, tmp_path):
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ImportProblem, match="workspace"):
        MappingCache().root  # noqa: B018 - resolving the root is the behaviour tested


def test_coordinator_caches_inside_its_workspace(setup_import, tmp_path):
    coordinator, _, _ = setup_import
    default_cache = CsvImportCoordinator(
        coordinator.client, 42, coordinator.snapshot, workspace_directory=tmp_path / "ws"
    ).cache
    assert default_cache.root == tmp_path / "ws" / "zybooks-mappings"


def test_propose_units_expands_section_ranges_in_descriptions(setup_import):
    """'Sections 2.12–2.14' means every section between, not just the endpoints."""
    coordinator, _, _ = setup_import
    assignments = [
        {"id": 102, "name": "U01A02: zyLabs", "description": "<p>Sections 2.12–2.14</p>"},
        {"id": 202, "name": "U02A02: zyLabs", "description": "<p>2.15 through 2.18</p>"},
    ]
    units = {unit.name: unit for unit in propose_units(coordinator.snapshot, assignments)}
    assert units["U01"].lab_sections == ("2.12", "2.13", "2.14")
    assert units["U02"].lab_sections == ("2.15", "2.16", "2.17", "2.18")


def lab_unit_for(snapshot, **overrides):
    return UnitMapping(
        "Unit",
        snapshot.sections,
        tuple(section for section in snapshot.sections if section != "2.1"),
        lab_target=102,
        distinct_labs_confirmed=True,
        **overrides,
    )


def test_labs_required_defaults_to_best_five():
    assert UnitMapping("Unit", (), ()).labs_required == importer.DEFAULT_LABS_REQUIRED == 5


@pytest.mark.parametrize(
    ("labs_required", "expected", "counted"),
    [
        (3, "90", 3),  # best three of seven: (100 + 90 + 80) / 3
        (5, "80", 5),  # the default best five
        (7, "70", 7),  # "complete every lab": all seven count
    ],
)
def test_labs_required_sets_how_many_best_labs_count(tmp_path, labs_required, expected, counted):
    snapshot = read_snapshot(write_csv(tmp_path / "scores.csv"))
    unit = lab_unit_for(snapshot, labs_required=labs_required)
    result = calculate(snapshot, snapshot.students[0], unit, "Lab", Decimal(100))
    assert result.points == Decimal(expected)
    assert sum(line.startswith("Counted") for line in result.breakdown) == counted


def test_ten_of_fifteen_labs_does_not_give_full_credit_for_five(tmp_path):
    """A two-units-a-week course asks for ten labs; five finished labs earn half."""
    labs = (100,) * 5 + (0,) * 10
    snapshot = read_snapshot(write_csv(tmp_path / "scores.csv", labs))
    student = snapshot.students[0]
    best_five = calculate(snapshot, student, lab_unit_for(snapshot), "Lab", Decimal(100))
    best_ten = calculate(
        snapshot,
        snapshot.students[0],
        lab_unit_for(snapshot, labs_required=10),
        "Lab",
        Decimal(100),
    )
    assert best_five.points == Decimal("100")
    assert best_ten.points == Decimal("50")


def test_lab_pool_smaller_than_labs_required_is_rejected(tmp_path):
    snapshot = read_snapshot(write_csv(tmp_path / "scores.csv"))
    with pytest.raises(ImportProblem, match="at least 10 distinct"):
        calculate(
            snapshot,
            snapshot.students[0],
            lab_unit_for(snapshot, labs_required=10),
            "Lab",
            Decimal(100),
        )


@pytest.mark.parametrize("labs_required", [0, -1, 2.5, "5"])
def test_labs_required_must_be_a_positive_whole_number(tmp_path, labs_required):
    snapshot = read_snapshot(write_csv(tmp_path / "scores.csv"))
    with pytest.raises(ImportProblem, match="whole number"):
        calculate(
            snapshot,
            snapshot.students[0],
            lab_unit_for(snapshot, labs_required=labs_required),
            "Lab",
            Decimal(100),
        )
