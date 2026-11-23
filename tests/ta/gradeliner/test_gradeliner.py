"""
Tests for GradeLiner Orchestrator and Components (``gradeliner/``)
================================================================

PURPOSE:
--------
Verifies the integration between Parser, Validator, and Orchestrator.
Checks score scaling, delimiter sniffing, and the full import preparation flow.
"""

from unittest.mock import MagicMock, patch

import pytest

from coursewright.ta.gradeliner.orchestrator import GradeLinerUpper
from coursewright.ta.gradeliner.parser import NetacadExportParser
from coursewright.ta.gradeliner.validator import GradeValidator


@pytest.fixture(autouse=True)
def run_in_temporary_directory(tmp_path, monkeypatch):
    """Mapping caches belong to a synthetic private workspace."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))


@pytest.fixture
def mock_canvas_client():
    """Returns a mocked Canvas client with a fake course."""
    client = MagicMock()
    client.get_course.return_value = {
        "id": 123,
        "name": "Test Course",
        "course_code": "COURSE-101",
    }
    # Default: no existing submissions (so nothing is "already graded").
    # Tests that need pre-existing grades override this per-assignment.
    client.get_assignment_submissions.return_value = []
    return client


@pytest.fixture
def mock_llm_client():
    """Returns a mocked LLM client."""
    return MagicMock()


# =============================================================================
# 1.  VALIDATOR TESTS
# =============================================================================


class TestGradeValidator:
    """Tests the numeric precision and safety checks of the GradeValidator."""

    def test_numeric_parsing(self):
        v = GradeValidator()
        assert v.parse_numeric_score("95.5") == 95.5
        assert v.parse_numeric_score("100%") == 100.0
        assert v.parse_numeric_score("-") == 0.0
        assert v.parse_numeric_score("N/A") == 0.0
        assert v.parse_numeric_score(None) == 0.0

    def test_blank_cells_are_not_recorded_scores(self):
        """A blank cell is "no score yet"; an explicit zero is a real score."""
        v = GradeValidator()
        for blank in (None, float("nan"), "", "  ", "-", "N/A", "null", "nan"):
            assert v.has_recorded_score(blank) is False
        for recorded in ("0", "0.0", "85", "100%", 0.0, 72.5):
            assert v.has_recorded_score(recorded) is True

    def test_numeric_parsing_handles_pandas_nan_gracefully(self):
        """
        Pandas represents missing CSV cells as float('nan') even with
        dtype=str.  parse_numeric_score must NOT propagate NaN — it
        should return 0.0 instead.
        """
        validator = GradeValidator()
        assert validator.parse_numeric_score(float("nan")) == 0.0
        assert validator.parse_numeric_score("nan") == 0.0

    def test_half_up_scaling(self):
        """Python's round() is banker's, we need half-up for grades."""
        v = GradeValidator()
        # 100/100 -> 50/50
        assert v.scale_score_half_up(100, 100, 50) == 50.0
        # 75/100 -> 37.5 -> 38.0
        assert v.scale_score_half_up(75, 100, 50) == 38.0
        # 74/100 -> 37.0
        assert v.scale_score_half_up(74, 100, 50) == 37.0

    def test_scale_score_handles_nan_input_without_crashing(self):
        """
        If a NaN somehow slips past parse_numeric_score, the scaler
        must return 0.0 instead of raising
        'cannot convert float NaN to integer'.
        """
        validator = GradeValidator()
        assert validator.scale_score_half_up(float("nan"), 100, 50) == 0.0

    def test_smoke_test_all_zeros(self):
        """Ensure the smoke test detects a likely mismatch."""
        v = GradeValidator()
        # 4 zeros out of 5 = 80% (threshold is > 80)
        passed, _ = v.perform_smoke_tests([0, 0, 0, 0, 10])
        assert passed is True

        # 5 zeros out of 5 = 100%
        passed, _ = v.perform_smoke_tests([0, 0, 0, 0, 0])
        assert passed is False


# =============================================================================
# 2.  PARSER TESTS
# =============================================================================


class TestNetacadParser:
    """Tests the robust delimiter sniffing and record cleaning."""

    def test_sniff_delimiter(self):
        p = NetacadExportParser()
        csv = "NAME,EMAIL,SCORE\nUser,email,100"
        tsv = "NAME\tEMAIL\tSCORE\nUser\temail\t100"

        assert p.sniff_delimiter_from_content(csv) == ","
        assert p.sniff_delimiter_from_content(tsv) == "\t"

    @patch("coursewright.ta.gradeliner.parser.pd.read_csv")
    def test_extract_clean_student_records(self, mock_read_csv, tmp_path):
        """Metadata and test students should be filtered out."""
        import pandas as pd

        df = pd.DataFrame(
            [
                {"NAME": "Points Possible", "EMAIL": "", "Score": "100"},
                {"NAME": "Real Student", "EMAIL": "real@edu", "Score": "90"},
                {"NAME": "Test Student", "EMAIL": "test@netacad", "Score": "0"},
            ]
        )
        mock_read_csv.return_value = df

        p = NetacadExportParser()
        dummy_file = tmp_path / "dummy.csv"
        dummy_file.write_text("dummy")

        p.parse_export_file(dummy_file)
        records = p.extract_clean_student_records()

        assert len(records) == 1
        assert records[0]["NAME"] == "Real Student"


# =============================================================================
# 3.  ORCHESTRATOR INTEGRATION
# =============================================================================


class TestGradeLinerOrchestrator:
    """Tests the top-level orchestrator logic."""

    def test_orchestrator_init(self, mock_canvas_client, mock_llm_client, tmp_path):
        """Init should fetch course details and setup folder names."""
        dummy_file = tmp_path / "grades.csv"
        dummy_file.touch()

        liner = GradeLinerUpper(mock_canvas_client, mock_llm_client, 123, dummy_file)

        assert liner.course_code == "COURSE-101"
        assert liner.course_name == "Test Course"
        assert mock_canvas_client.get_course.called

    def test_prepare_grade_import_with_cached_mappings(
        self,
        mock_canvas_client,
        tmp_path,
    ):
        """
        End-to-end test of the weekly fast-path: parse a NetAcad CSV,
        load cached mappings, and produce GradeImportRow objects.

        This is the most critical codepath — it runs every week and
        determines what grades get submitted to Canvas.
        """
        # 1. Create a realistic NetAcad export CSV with a mix of
        #    real scores, blanks, and NaN-triggering empty cells
        netacad_csv = tmp_path / "netacad_export.csv"
        netacad_csv.write_text(
            "NAME,EMAIL,Module 1 Exam,Module 2 Exam\n"
            "Smith John,jsmith@edu,85,90\n"
            "Doe Jane,jdoe@edu,75,\n"  # blank = pandas NaN
            "Test Student,test@netacad,0,0\n"
        )

        # 2. Create cached assignment mapping
        import json
        from pathlib import Path

        course_folder = Path("exports") / "COURSE-101"
        course_folder.mkdir(parents=True, exist_ok=True)

        assignment_cache = {
            "course_id": 123,
            "canvas_course_name": "Test Course",
            "created_at": "2026-02-13T00:00:00",
            "mappings": [
                {
                    "netacad_column": "Module 1 Exam",
                    "canvas_assignment_name": "U01 Module 1 Exam",
                    "canvas_assignment_id": 1001,
                    "canvas_points_possible": 50.0,
                    "netacad_points_possible": 100.0,
                    "confidence": "high",
                },
                {
                    "netacad_column": "Module 2 Exam",
                    "canvas_assignment_name": "U02 Module 2 Exam",
                    "canvas_assignment_id": 1002,
                    "canvas_points_possible": 100.0,
                    "netacad_points_possible": 100.0,
                    "confidence": "high",
                },
            ],
            "unmapped_netacad": [],
            "unmapped_canvas": [],
        }

        student_cache = {
            "course_id": 123,
            "created_at": "2026-02-13T00:00:00",
            "students": [
                {
                    "netacad_name": "Smith John",
                    "netacad_email": "jsmith@edu",
                    "canvas_user_id": 501,
                    "canvas_name": "John Smith",
                    "canvas_login_id": "jsmith",
                    "match_method": "email_exact",
                    "confidence": "high",
                },
                {
                    "netacad_name": "Doe Jane",
                    "netacad_email": "jdoe@edu",
                    "canvas_user_id": 502,
                    "canvas_name": "Jane Doe",
                    "canvas_login_id": "jdoe",
                    "match_method": "email_exact",
                    "confidence": "high",
                },
            ],
            "unmatched_netacad": ["Test Student"],
            "unmatched_canvas": [],
        }

        (course_folder / "gradeliner_assignment_map.json").write_text(json.dumps(assignment_cache))
        (course_folder / "gradeliner_student_map.json").write_text(json.dumps(student_cache))

        try:
            # 3. Create the orchestrator (no LLM needed for cached path)
            orchestrator = GradeLinerUpper(
                mock_canvas_client,
                None,
                123,
                netacad_csv,
            )

            # 4. Run prepare_grade_import — this is the function under test
            import_rows = orchestrator.prepare_grade_import()

            # 5. Assertions
            # Two matched students × 2 assignments, minus Doe's blank cell = 3 rows
            assert len(import_rows) == 3, f"Expected 3 import rows, got {len(import_rows)}"

            # Smith's Module 1: 85/100 → 42.5 → 43 (half-up) out of 50
            smith_mod1 = [
                r for r in import_rows if r.canvas_user_id == 501 and r.assignment_id == 1001
            ]
            assert len(smith_mod1) == 1
            assert smith_mod1[0].netacad_score == 85.0
            assert smith_mod1[0].scaled_score == 43.0  # half-up

            # Smith's Module 2: 90/100 → 90/100 (same scale)
            smith_mod2 = [
                r for r in import_rows if r.canvas_user_id == 501 and r.assignment_id == 1002
            ]
            assert len(smith_mod2) == 1
            assert smith_mod2[0].scaled_score == 90.0

            # Doe's Module 2: blank cell → NaN → "not attempted yet".  It must
            # produce no row at all: importing it as 0 would zero an exam the
            # student has not taken (and every exam that is not due yet).
            doe_mod2 = [
                r for r in import_rows if r.canvas_user_id == 502 and r.assignment_id == 1002
            ]
            assert doe_mod2 == [], "A blank NetAcad cell must never become a zero"

            # Test Student should NOT appear (filtered by parser)
            test_rows = [r for r in import_rows if r.canvas_name == "Test Student"]
            assert len(test_rows) == 0, "Test students must be filtered out"
        finally:
            # Clean up the exports folder we created
            import shutil

            if course_folder.exists():
                shutil.rmtree(course_folder)

    def test_prepare_grade_import_with_assignment_filter(
        self,
        mock_canvas_client,
        tmp_path,
    ):
        """
        When selected_assignment_ids is provided, only rows for those
        assignments should appear in the result — all others are skipped.
        """
        netacad_csv = tmp_path / "netacad.csv"
        netacad_csv.write_text("NAME,EMAIL,Exam A,Exam B\nAlpha Stu,alpha@edu,80,60\n")

        import json
        from pathlib import Path

        course_folder = Path("exports") / "COURSE-101"
        course_folder.mkdir(parents=True, exist_ok=True)

        assignment_cache = {
            "course_id": 123,
            "canvas_course_name": "Test Course",
            "created_at": "2026-02-13T00:00:00",
            "mappings": [
                {
                    "netacad_column": "Exam A",
                    "canvas_assignment_name": "Canvas Exam A",
                    "canvas_assignment_id": 2001,
                    "canvas_points_possible": 100.0,
                    "netacad_points_possible": 100.0,
                    "confidence": "high",
                },
                {
                    "netacad_column": "Exam B",
                    "canvas_assignment_name": "Canvas Exam B",
                    "canvas_assignment_id": 2002,
                    "canvas_points_possible": 50.0,
                    "netacad_points_possible": 100.0,
                    "confidence": "high",
                },
            ],
            "unmapped_netacad": [],
            "unmapped_canvas": [],
        }

        student_cache = {
            "course_id": 123,
            "created_at": "2026-02-13T00:00:00",
            "students": [
                {
                    "netacad_name": "Alpha Stu",
                    "netacad_email": "alpha@edu",
                    "canvas_user_id": 601,
                    "canvas_name": "Alpha Student",
                    "canvas_login_id": "alpha",
                    "match_method": "email_exact",
                    "confidence": "high",
                },
            ],
            "unmatched_netacad": [],
            "unmatched_canvas": [],
        }

        (course_folder / "gradeliner_assignment_map.json").write_text(json.dumps(assignment_cache))
        (course_folder / "gradeliner_student_map.json").write_text(json.dumps(student_cache))

        try:
            orchestrator = GradeLinerUpper(
                mock_canvas_client,
                None,
                123,
                netacad_csv,
            )

            # Only select Exam A (id 2001) — Exam B should be excluded
            rows = orchestrator.prepare_grade_import(
                selected_assignment_ids={2001},
            )

            assert len(rows) == 1, f"Expected 1 row (only Exam A), got {len(rows)}"
            assert rows[0].assignment_id == 2001
            assert rows[0].scaled_score == 80.0

            # Passing None should include everything (2 rows)
            all_rows = orchestrator.prepare_grade_import(
                selected_assignment_ids=None,
            )
            assert len(all_rows) == 2

        finally:
            import shutil

            if course_folder.exists():
                shutil.rmtree(course_folder)

    def test_prepare_grade_import_skips_already_graded_students(
        self,
        mock_canvas_client,
        tmp_path,
    ):
        """
        When a student already has a score in Canvas for an assignment,
        prepare_grade_import should skip that (assignment, student) pair
        to prevent duplicate grade posts and stacking comments.

        This is the regression test for the bug where every run of the
        GradeLinErUpper would add another "Imported from NetAcad" comment
        on top of the existing grade.
        """
        from coursewright.core.canvas.models import Submission

        netacad_csv = tmp_path / "netacad.csv"
        netacad_csv.write_text(
            "NAME,EMAIL,Exam A,Exam B\nAlpha Stu,alpha@edu,80,60\nBeta Stu,beta@edu,90,70\n"
        )

        import json
        from pathlib import Path

        course_folder = Path("exports") / "COURSE-101"
        course_folder.mkdir(parents=True, exist_ok=True)

        assignment_cache = {
            "course_id": 123,
            "canvas_course_name": "Test Course",
            "created_at": "2026-02-13T00:00:00",
            "mappings": [
                {
                    "netacad_column": "Exam A",
                    "canvas_assignment_name": "Canvas Exam A",
                    "canvas_assignment_id": 2001,
                    "canvas_points_possible": 100.0,
                    "netacad_points_possible": 100.0,
                    "confidence": "high",
                },
                {
                    "netacad_column": "Exam B",
                    "canvas_assignment_name": "Canvas Exam B",
                    "canvas_assignment_id": 2002,
                    "canvas_points_possible": 50.0,
                    "netacad_points_possible": 100.0,
                    "confidence": "high",
                },
            ],
            "unmapped_netacad": [],
            "unmapped_canvas": [],
        }

        student_cache = {
            "course_id": 123,
            "created_at": "2026-02-13T00:00:00",
            "students": [
                {
                    "netacad_name": "Alpha Stu",
                    "netacad_email": "alpha@edu",
                    "canvas_user_id": 601,
                    "canvas_name": "Alpha Student",
                    "canvas_login_id": "alpha",
                    "match_method": "email_exact",
                    "confidence": "high",
                },
                {
                    "netacad_name": "Beta Stu",
                    "netacad_email": "beta@edu",
                    "canvas_user_id": 602,
                    "canvas_name": "Beta Student",
                    "canvas_login_id": "beta",
                    "match_method": "email_exact",
                    "confidence": "high",
                },
            ],
            "unmatched_netacad": [],
            "unmatched_canvas": [],
        }

        (course_folder / "gradeliner_assignment_map.json").write_text(json.dumps(assignment_cache))
        (course_folder / "gradeliner_student_map.json").write_text(json.dumps(student_cache))

        # Simulate: Alpha already has a grade for Exam A (2001) in Canvas.
        # Beta has no grades yet.  Nobody has a grade for Exam B (2002).
        def fake_get_submissions(course_id, assignment_id):
            if assignment_id == 2001:
                return [
                    Submission(
                        id=9001,
                        user_id=601,
                        assignment_id=2001,
                        workflow_state="graded",
                        score=80.0,
                        grade="80",
                    ),
                    Submission(
                        id=9002,
                        user_id=602,
                        assignment_id=2001,
                        workflow_state="unsubmitted",
                        score=None,
                        grade=None,
                    ),
                ]
            # Exam B: no one graded yet
            return [
                Submission(
                    id=9003,
                    user_id=601,
                    assignment_id=2002,
                    workflow_state="submitted",
                    score=None,
                    grade=None,
                ),
                Submission(
                    id=9004,
                    user_id=602,
                    assignment_id=2002,
                    workflow_state="submitted",
                    score=None,
                    grade=None,
                ),
            ]

        mock_canvas_client.get_assignment_submissions.side_effect = fake_get_submissions

        try:
            orchestrator = GradeLinerUpper(
                mock_canvas_client,
                None,
                123,
                netacad_csv,
            )

            rows = orchestrator.prepare_grade_import()

            # Without the fix: 2 students × 2 assignments = 4 rows (WRONG)
            # With the fix:    Alpha skipped on Exam A = 3 rows (CORRECT)
            assert len(rows) == 3, (
                f"Expected 3 rows (Alpha's Exam A should be skipped), got {len(rows)}"
            )

            # Alpha should NOT appear for Exam A (already graded)
            alpha_exam_a = [r for r in rows if r.canvas_user_id == 601 and r.assignment_id == 2001]
            assert len(alpha_exam_a) == 0, "Alpha's Exam A is already graded — must be skipped"

            # Alpha SHOULD appear for Exam B (not yet graded)
            alpha_exam_b = [r for r in rows if r.canvas_user_id == 601 and r.assignment_id == 2002]
            assert len(alpha_exam_b) == 1

            # Beta should appear for both
            beta_rows = [r for r in rows if r.canvas_user_id == 602]
            assert len(beta_rows) == 2

        finally:
            import shutil

            if course_folder.exists():
                shutil.rmtree(course_folder)

    def test_prepare_grade_import_regrades_placeholder_zero(
        self,
        mock_canvas_client,
        tmp_path,
    ):
        """A score of 0.0 with workflow_state == "submitted" is a
        placeholder left behind by a previous failed run (or auto-zero
        from Canvas).  The Canvas gradebook UI still shows those rows
        as ungraded, so prepare_grade_import must NOT skip them — the
        real NetAcad score should be re-posted on top.

        Regression test for the a unit-coded case where 3/5 students had
        score=0.0 + state=submitted and the wizard preview came up
        empty.
        """
        from coursewright.core.canvas.models import Submission

        netacad_csv = tmp_path / "netacad.csv"
        netacad_csv.write_text("NAME,EMAIL,Exam A\nAlpha Stu,alpha@edu,84\n")

        import json
        from pathlib import Path

        course_folder = Path("exports") / "COURSE-101"
        course_folder.mkdir(parents=True, exist_ok=True)

        assignment_cache = {
            "course_id": 123,
            "canvas_course_name": "Test Course",
            "created_at": "2026-02-13T00:00:00",
            "mappings": [
                {
                    "netacad_column": "Exam A",
                    "canvas_assignment_name": "Canvas Exam A",
                    "canvas_assignment_id": 2001,
                    "canvas_points_possible": 200.0,
                    "netacad_points_possible": 100.0,
                    "confidence": "high",
                },
            ],
            "unmapped_netacad": [],
            "unmapped_canvas": [],
        }
        student_cache = {
            "course_id": 123,
            "created_at": "2026-02-13T00:00:00",
            "students": [
                {
                    "netacad_name": "Alpha Stu",
                    "netacad_email": "alpha@edu",
                    "canvas_user_id": 601,
                    "canvas_name": "Alpha Student",
                    "canvas_login_id": "alpha",
                    "match_method": "email_exact",
                    "confidence": "high",
                },
            ],
            "unmatched_netacad": [],
            "unmatched_canvas": [],
        }
        (course_folder / "gradeliner_assignment_map.json").write_text(json.dumps(assignment_cache))
        (course_folder / "gradeliner_student_map.json").write_text(json.dumps(student_cache))

        # Placeholder zero from a previous failed run.
        mock_canvas_client.get_assignment_submissions.return_value = [
            Submission(
                id=9001,
                user_id=601,
                assignment_id=2001,
                workflow_state="submitted",
                score=0.0,
                grade="0",
            ),
        ]

        try:
            orchestrator = GradeLinerUpper(
                mock_canvas_client,
                None,
                123,
                netacad_csv,
            )
            rows = orchestrator.prepare_grade_import()

            assert len(rows) == 1, "Placeholder zero must NOT be treated as already-graded"
            assert rows[0].canvas_user_id == 601
            assert rows[0].scaled_score == 168.0  # 84/100 * 200 = 168
        finally:
            import shutil

            if course_folder.exists():
                shutil.rmtree(course_folder)

    def test_summarize_assignment_coverage(
        self,
        mock_canvas_client,
        tmp_path,
    ):
        """
        summarize_assignment_coverage should report how many matched
        students have non-zero scores for each mapped assignment.
        """
        netacad_csv = tmp_path / "netacad.csv"
        netacad_csv.write_text(
            "NAME,EMAIL,Quiz 1,Quiz 2\nStu A,a@edu,90,0\nStu B,b@edu,85,0\nStu C,c@edu,0,0\n"
        )

        import json
        from pathlib import Path

        course_folder = Path("exports") / "COURSE-101"
        course_folder.mkdir(parents=True, exist_ok=True)

        assignment_cache = {
            "course_id": 123,
            "canvas_course_name": "Test Course",
            "created_at": "2026-02-13T00:00:00",
            "mappings": [
                {
                    "netacad_column": "Quiz 1",
                    "canvas_assignment_name": "Canvas Quiz 1",
                    "canvas_assignment_id": 3001,
                    "canvas_points_possible": 50.0,
                    "netacad_points_possible": 100.0,
                    "confidence": "high",
                },
                {
                    "netacad_column": "Quiz 2",
                    "canvas_assignment_name": "Canvas Quiz 2",
                    "canvas_assignment_id": 3002,
                    "canvas_points_possible": 50.0,
                    "netacad_points_possible": 100.0,
                    "confidence": "high",
                },
            ],
            "unmapped_netacad": [],
            "unmapped_canvas": [],
        }

        student_cache = {
            "course_id": 123,
            "created_at": "2026-02-13T00:00:00",
            "students": [
                {
                    "netacad_name": "Stu A",
                    "netacad_email": "a@edu",
                    "canvas_user_id": 701,
                    "canvas_name": "Student A",
                    "canvas_login_id": "stua",
                    "match_method": "email_exact",
                    "confidence": "high",
                },
                {
                    "netacad_name": "Stu B",
                    "netacad_email": "b@edu",
                    "canvas_user_id": 702,
                    "canvas_name": "Student B",
                    "canvas_login_id": "stub",
                    "match_method": "email_exact",
                    "confidence": "high",
                },
                {
                    "netacad_name": "Stu C",
                    "netacad_email": "c@edu",
                    "canvas_user_id": 703,
                    "canvas_name": "Student C",
                    "canvas_login_id": "stuc",
                    "match_method": "email_exact",
                    "confidence": "high",
                },
            ],
            "unmatched_netacad": [],
            "unmatched_canvas": [],
        }

        (course_folder / "gradeliner_assignment_map.json").write_text(json.dumps(assignment_cache))
        (course_folder / "gradeliner_student_map.json").write_text(json.dumps(student_cache))

        try:
            orchestrator = GradeLinerUpper(
                mock_canvas_client,
                None,
                123,
                netacad_csv,
            )
            coverage = orchestrator.summarize_assignment_coverage()

            assert len(coverage) == 2

            # Quiz 1: 2 of 3 students have scores → ~66.7%
            quiz1 = coverage[0]
            assert quiz1["canvas_assignment_id"] == 3001
            assert quiz1["students_with_scores"] == 2
            assert quiz1["total_students"] == 3
            assert 66 < quiz1["score_percentage"] < 68  # ~66.67%

            # Quiz 2: 0 of 3 students have scores → 0%
            quiz2 = coverage[1]
            assert quiz2["canvas_assignment_id"] == 3002
            assert quiz2["students_with_scores"] == 0
            assert quiz2["score_percentage"] == 0.0

        finally:
            import shutil

            if course_folder.exists():
                shutil.rmtree(course_folder)
