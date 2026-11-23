"""Tests for the MindTap backfill library (``ta/gradeliner/mindtap.py``).

These cover CSV parsing, score-basis auto-detection, name normalization,
fuzzy assignment matching, grade-plan construction and the safe apply
step.  None of them touch a real Canvas, so they run fully offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coursewright.ta.gradeliner import mindtap as backfill


@dataclass
class FakeAssignment:
    """Stand-in for the project's Canvas Assignment model."""

    id: int
    name: str
    points_possible: float | None


SAMPLE_CSV = (
    (
        "User ID,Student Name,Student ID,Total Points to Date,Overall Score To Date,"
        "Quiz One,Lab Two,Sim Three\n"
    )
    + """\
,,,,,Assessment,Assessment,Assessment
,,,140.0,93.33%,Max Points: 100.0,Max Points: 50.0,Max Points: 50.0
100,"Example, Alex",,140.0,93.33%,90,50,
200,"Sample, Sam",,50.0,50.0%,50,,0
"""
)


def _write_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "MindTap_Course_Gradebook_2026.csv"
    csv_path.write_text(SAMPLE_CSV, encoding="utf-8")
    return csv_path


def test_parse_mindtap_gradebook(tmp_path):
    gradebook = backfill.parse_mindtap_gradebook(_write_csv(tmp_path))
    assert gradebook.assignment_names == ["Quiz One", "Lab Two", "Sim Three"]
    assert gradebook.max_points == [100.0, 50.0, 50.0]
    assert "Example, Alex" in gradebook.students
    assert gradebook.students["Example, Alex"]["total"] == 140.0
    assert gradebook.students["Example, Alex"]["scores"] == [90.0, 50.0, None]


def test_extract_student_scores_matches_substring(tmp_path):
    gradebook = backfill.parse_mindtap_gradebook(_write_csv(tmp_path))
    student = backfill.extract_student_scores(gradebook, "Alex")
    assert student is not None
    assert student.name == "Example, Alex"
    assert student.reported_total == 140.0


def test_detect_score_basis_points():
    # 90 + 50 == 140 reported total -> cells are raw points.
    basis = backfill.detect_score_basis([90.0, 50.0, None], [100.0, 50.0, 50.0], 140.0)
    assert basis == "points"


def test_detect_score_basis_percent():
    # 0.9*100 + 1.0*50 == 140 -> cells are percentages.
    basis = backfill.detect_score_basis([90.0, 100.0, None], [100.0, 50.0, 50.0], 140.0)
    assert basis == "percent"


def test_normalize_assignment_name():
    assert backfill.normalize_assignment_name("Virtual Lab 1-1, Guided!") == (
        "virtual lab 1 1 guided"
    )


def test_match_assignment_exact_and_fuzzy():
    canvas = [
        FakeAssignment(1, "Quiz One", 100.0),
        FakeAssignment(2, "Lab Two", 50.0),
    ]
    by_norm = {backfill.normalize_assignment_name(a.name): a for a in canvas}

    exact, ratio = backfill.match_assignment("Quiz One", by_norm, 0.82)
    assert exact.id == 1
    assert ratio == 1.0

    fuzzy, ratio = backfill.match_assignment("Quiz  One!!", by_norm, 0.82)
    assert fuzzy.id == 1

    none_match, _ = backfill.match_assignment("Totally Unrelated", by_norm, 0.82)
    assert none_match is None


def test_build_grade_plan_scales_and_respects_only_missing(tmp_path):
    gradebook = backfill.parse_mindtap_gradebook(_write_csv(tmp_path))
    student = backfill.extract_student_scores(gradebook, "Alex")

    canvas = [
        FakeAssignment(10, "Quiz One", 20.0),  # Canvas out of 20, MindTap out of 100
        FakeAssignment(11, "Lab Two", 50.0),
    ]
    current = {10: None, 11: 25.0}  # Quiz ungraded; Lab already has a grade.

    changes, unmatched = backfill.build_grade_plan(
        student=student,
        gradebook=gradebook,
        canvas_assignments=canvas,
        current_scores_by_assignment=current,
        basis="points",
        threshold=0.82,
        overwrite=False,
    )

    by_name = {c.canvas_assignment_name: c for c in changes}
    # 90/100 * 20 = 18, and Quiz was ungraded -> FILL.
    assert by_name["Quiz One"].new_score == 18.0
    assert by_name["Quiz One"].action == "FILL"
    # Lab already had a grade and overwrite is False -> skipped.
    assert by_name["Lab Two"].action == "SKIP-HAS-GRADE"


def test_build_grade_plan_overwrite_updates_existing(tmp_path):
    gradebook = backfill.parse_mindtap_gradebook(_write_csv(tmp_path))
    student = backfill.extract_student_scores(gradebook, "Alex")
    canvas = [FakeAssignment(11, "Lab Two", 50.0)]

    changes, _ = backfill.build_grade_plan(
        student=student,
        gradebook=gradebook,
        canvas_assignments=canvas,
        current_scores_by_assignment={11: 25.0},
        basis="points",
        threshold=0.82,
        overwrite=True,
    )
    assert changes[0].action == "UPDATE"
    assert changes[0].new_score == 50.0
