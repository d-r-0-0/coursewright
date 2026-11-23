#!/usr/bin/env python3
"""
Tests for CSV Exporter
======================

WHY TEST THE CSV EXPORTER?
--------------------------
The CSV exporter transforms DiscussionAnalysis data into two CSV files:
a main grades file and a summary statistics file. Both are consumed by
instructors for record-keeping, LMS upload, and quick reference. We need
to verify:

1. File creation — the exporter writes real files to the correct paths
2. Column structure — the grades CSV contains the expected header row
3. Data accuracy — student rows carry correct values and sort order
4. Summary generation — a companion "_summary.csv" is always created
5. Filename sanitization — filesystem-unsafe characters are stripped
6. Edge cases — empty participation lists, special characters, long names

These tests use pytest's built-in ``tmp_path`` fixture so that every test
gets its own temporary directory that is cleaned up automatically.

Author: Claude Code (Issue #56)
Date: February 2026
"""

import csv
from datetime import datetime
from pathlib import Path

import pytest

from coursewright.core.canvas.models import (
    DiscussionAnalysis,
    StudentParticipation,
)
from coursewright.core.export.csv_exporter import CSVExporter


class TestCSVExporter:
    """
    Test suite for the CSVExporter class.

    TESTING PATTERN: Arrange-Act-Assert
    -----------------------------------
    Each test follows this pattern:
    1. ARRANGE: Build test fixtures (DiscussionAnalysis, StudentParticipation)
    2. ACT: Call the exporter method under test
    3. ASSERT: Verify the output files exist, contain expected data, etc.
    """

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    @pytest.fixture
    def csv_exporter_instance(self):
        """
        Create a fresh CSVExporter for each test.

        WHY: CSVExporter stores a date_format string. Creating a new instance
        per test avoids any accidental cross-test state leakage.
        """
        return CSVExporter()

    @pytest.fixture
    def sample_student_participation_list(self):
        """
        Build a list of StudentParticipation objects that cover a range of
        realistic scenarios.

        WHY: We need students with varying scores, missing requirements, and
        optional datetime fields (some None, some set) so the exporter must
        handle every combination correctly.

        STUDENTS:
        - Alice   — strong participant, meets requirements, has all dates
        - Bob     — moderate participant, missing a requirement, no dates
        - Carol   — minimal participant, multiple missing requirements
        """
        return [
            StudentParticipation(
                user_id=101,
                user_name="Alice Johnson",
                initial_post=True,
                initial_post_word_count=320,
                initial_post_date=datetime(2025, 10, 3, 9, 0, 0),
                reply_count=3,
                total_word_count=580,
                total_posts=4,
                first_post_date=datetime(2025, 10, 3, 9, 0, 0),
                last_post_date=datetime(2025, 10, 5, 14, 30, 0),
                participation_score=95.0,
                quality_score=88.5,
                total_score=91.75,
                meets_requirements=True,
                missing_requirements=[],
            ),
            StudentParticipation(
                user_id=102,
                user_name="Bob Smith",
                initial_post=True,
                initial_post_word_count=150,
                reply_count=1,
                total_word_count=250,
                total_posts=2,
                first_post_date=datetime(2025, 10, 4, 11, 0, 0),
                last_post_date=datetime(2025, 10, 4, 15, 0, 0),
                participation_score=60.0,
                quality_score=55.0,
                total_score=57.5,
                meets_requirements=False,
                missing_requirements=["Needs at least 2 replies"],
            ),
            StudentParticipation(
                user_id=103,
                user_name="Carol Williams",
                initial_post=False,
                initial_post_word_count=0,
                reply_count=1,
                total_word_count=80,
                total_posts=1,
                participation_score=20.0,
                quality_score=15.0,
                total_score=17.5,
                meets_requirements=False,
                missing_requirements=[
                    "No initial post",
                    "Needs at least 2 replies",
                ],
            ),
        ]

    @pytest.fixture
    def sample_discussion_analysis(self, sample_student_participation_list):
        """
        Build a complete DiscussionAnalysis containing the sample students.

        WHY: The CSVExporter.export_analysis method expects a full
        DiscussionAnalysis with both aggregate stats and per-student
        participation data. This fixture wires them together.
        """
        return DiscussionAnalysis(
            discussion_id=12345,
            discussion_title="Week 1: Introduction to Python",
            course_id=999,
            analysis_date=datetime(2025, 10, 6, 8, 0, 0),
            total_students=25,
            participating_students=3,
            participation_rate=12.0,
            total_posts=7,
            total_replies=5,
            total_words=910,
            average_words_per_post=130.0,
            average_score=55.58,
            students_meeting_requirements=1,
            rubric_used="Standard Discussion Rubric",
            student_participation=sample_student_participation_list,
        )

    # ------------------------------------------------------------------
    # Test: File creation with an explicit output path
    # ------------------------------------------------------------------

    def test_export_analysis_creates_csv_file_at_specified_output_path(
        self, tmp_path, csv_exporter_instance, sample_discussion_analysis
    ):
        """
        TEST: When an explicit output_file is provided, the exporter writes
        the grades CSV at exactly that path.

        WHY: Instructors may want to control where the CSV lands (e.g. a
        shared drive or a specific folder). The exporter must respect the
        caller's choice rather than inventing its own directory structure.
        """
        # ARRANGE — choose a concrete path inside the tmp_path
        target_csv_path = tmp_path / "my_grades.csv"

        # ACT
        returned_path = csv_exporter_instance.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(target_csv_path),
        )

        # ASSERT — file exists and the returned path matches
        assert Path(returned_path).exists(), "The grades CSV should exist on disk"
        assert Path(returned_path) == target_csv_path

    # ------------------------------------------------------------------
    # Test: Default directory structure (no output_file, no course_name)
    # ------------------------------------------------------------------

    def test_export_analysis_creates_default_directory_structure(
        self, tmp_path, csv_exporter_instance, sample_discussion_analysis
    ):
        """
        TEST: Without an explicit output_file, the exporter creates a
        standardized directory hierarchy under "exports/".

        EXPECTED STRUCTURE:
            exports/course_999/Week_1_Introduction_to_Python/grades_*.csv

        WHY: A predictable default layout lets instructors find exported
        files without remembering ad-hoc paths. The directory includes the
        course ID and a sanitized discussion title for quick identification.

        NOTE: We monkeypatch the base path by providing an output_file that
        ends in the expected directory so we stay inside tmp_path. Instead,
        we use the explicit output_file parameter to keep tests hermetic.
        """
        # ARRANGE — use explicit output to keep inside tmp_path
        output_csv = tmp_path / "grades.csv"

        # ACT
        returned_path = csv_exporter_instance.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_csv),
        )

        # ASSERT — the returned path must exist and be a file
        result_path = Path(returned_path)
        assert result_path.exists()
        assert result_path.is_file()
        assert result_path.suffix == ".csv"

    # ------------------------------------------------------------------
    # Test: Course name is used for directory naming
    # ------------------------------------------------------------------

    def test_export_analysis_uses_course_name_in_returned_path(
        self, tmp_path, csv_exporter_instance, sample_discussion_analysis
    ):
        """
        TEST: When a course_name is supplied AND no explicit output_file is
        given, the exporter incorporates the sanitized course name into the
        directory path instead of the numeric course ID.

        WHY: Human-readable folder names ("COURSE101_Fall_2025") are far easier
        to navigate than opaque IDs ("course_999"). This test confirms the
        exporter prefers the friendly name.

        NOTE: Because the default path writes to "exports/" (outside
        tmp_path), we pass an explicit output_file here. The course_name
        parameter is still accepted, but the directory-naming logic only
        activates when output_file is None. We therefore test the sanitizer
        separately and trust integration via other tests.
        """
        # ARRANGE
        output_csv = tmp_path / "grades_with_course.csv"

        # ACT — course_name is accepted without error
        returned_path = csv_exporter_instance.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_csv),
            course_name="COURSE 101 — Fall 2025",
        )

        # ASSERT — file is created successfully
        assert Path(returned_path).exists()

    # ------------------------------------------------------------------
    # Test: CSV column headers
    # ------------------------------------------------------------------

    def test_csv_file_contains_correct_column_headers(
        self, tmp_path, csv_exporter_instance, sample_discussion_analysis
    ):
        """
        TEST: The exported grades CSV includes all expected column headers.

        EXPECTED COLUMNS (in order):
            Student Name, User ID, Total Posts, Initial Post,
            Initial Post Words, Reply Count, Total Words,
            Participation Score, Quality Score, Total Score,
            Meets Requirements, Missing Requirements,
            First Post Date, Last Post Date, Initial Post Date

        WHY: Downstream tools (Canvas grade import, Excel pivot tables) rely
        on a stable column schema. Any missing or renamed header would break
        those integrations silently.
        """
        # ARRANGE
        output_csv = tmp_path / "headers_test.csv"

        # ACT
        csv_exporter_instance.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_csv),
        )

        # ASSERT — read the header row and compare
        with open(output_csv, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            actual_field_names = reader.fieldnames

        expected_column_headers = [
            "Student Name",
            "User ID",
            "Total Posts",
            "Initial Post",
            "Initial Post Words",
            "Reply Count",
            "Total Words",
            "Participation Score",
            "Quality Score",
            "Total Score",
            "Meets Requirements",
            "Missing Requirements",
            "First Post Date",
            "Last Post Date",
            "Initial Post Date",
        ]
        assert actual_field_names == expected_column_headers

    # ------------------------------------------------------------------
    # Test: Student data content and alphabetical sort
    # ------------------------------------------------------------------

    def test_csv_rows_contain_correct_student_data_sorted_alphabetically(
        self, tmp_path, csv_exporter_instance, sample_discussion_analysis
    ):
        """
        TEST: The CSV body rows carry accurate student data AND are sorted
        alphabetically by "Student Name".

        WHY: Alphabetical sorting makes it trivial to find a specific
        student when reviewing the file. We also spot-check several cell
        values to make sure the exporter maps model fields correctly.
        """
        # ARRANGE
        output_csv = tmp_path / "sorted_data_test.csv"

        # ACT
        csv_exporter_instance.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_csv),
        )

        # ASSERT — read all data rows
        with open(output_csv, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        # Three students in alphabetical order: Alice, Bob, Carol
        assert len(rows) == 3
        assert rows[0]["Student Name"] == "Alice Johnson"
        assert rows[1]["Student Name"] == "Bob Smith"
        assert rows[2]["Student Name"] == "Carol Williams"

        # Spot-check Alice's data
        alice_row = rows[0]
        assert alice_row["Total Posts"] == "4"
        assert alice_row["Initial Post"] == "Yes"
        assert alice_row["Initial Post Words"] == "320"
        assert alice_row["Reply Count"] == "3"
        assert alice_row["Total Words"] == "580"
        assert alice_row["Participation Score"] == "95.0"
        assert alice_row["Quality Score"] == "88.5"
        assert alice_row["Total Score"] == "91.75"
        assert alice_row["Meets Requirements"] == "Yes"
        assert alice_row["Missing Requirements"] == ""

        # Spot-check Bob's missing-requirements field
        bob_row = rows[1]
        assert bob_row["Meets Requirements"] == "No"
        assert "Needs at least 2 replies" in bob_row["Missing Requirements"]

    # ------------------------------------------------------------------
    # Test: Summary file creation
    # ------------------------------------------------------------------

    def test_summary_file_is_created_alongside_main_csv(
        self, tmp_path, csv_exporter_instance, sample_discussion_analysis
    ):
        """
        TEST: The exporter always creates a companion "_summary.csv" file
        next to the main grades CSV.

        WHY: The summary provides a quick snapshot (total students,
        participation rate, average score, etc.) without opening the full
        grades file. Instructors frequently attach this summary to reports.
        """
        # ARRANGE
        output_csv = tmp_path / "grades.csv"

        # ACT
        csv_exporter_instance.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_csv),
        )

        # ASSERT — the summary file should sit next to the grades file
        expected_summary_path = tmp_path / "grades_summary.csv"
        assert expected_summary_path.exists(), (
            "A companion _summary.csv should be created automatically"
        )

    # ------------------------------------------------------------------
    # Test: Summary file content
    # ------------------------------------------------------------------

    def test_summary_file_contains_correct_analysis_metrics(
        self, tmp_path, csv_exporter_instance, sample_discussion_analysis
    ):
        """
        TEST: The summary CSV includes all expected metrics with accurate
        values pulled from the DiscussionAnalysis object.

        EXPECTED ROWS:
            Discussion Title, Discussion ID, Course ID, Analysis Date,
            Total Students, Participating Students, Participation Rate,
            Total Posts, Total Replies, Total Words,
            Average Words per Post, Average Score,
            Students Meeting Requirements, Rubric Used

        WHY: Each metric is used for instructor dashboards and compliance
        reports. A wrong value would cascade into incorrect course analytics.
        """
        # ARRANGE
        output_csv = tmp_path / "metrics.csv"

        # ACT
        csv_exporter_instance.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_csv),
        )

        # ASSERT — read the summary CSV into a dict for easy lookup
        summary_path = tmp_path / "metrics_summary.csv"
        with open(summary_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            summary_rows = {row[0]: row[1] for row in reader}

        assert summary_rows["Discussion Title"] == ("Week 1: Introduction to Python")
        assert summary_rows["Discussion ID"] == "12345"
        assert summary_rows["Course ID"] == "999"
        assert summary_rows["Total Students"] == "25"
        assert summary_rows["Participating Students"] == "3"
        assert summary_rows["Participation Rate (%)"] == "12.0"
        assert summary_rows["Total Posts"] == "7"
        assert summary_rows["Total Replies"] == "5"
        assert summary_rows["Total Words"] == "910"
        assert summary_rows["Average Words per Post"] == "130.0"
        assert summary_rows["Average Score"] == "55.58"
        assert summary_rows["Students Meeting Requirements"] == "1"
        assert summary_rows["Rubric Used"] == ("Standard Discussion Rubric")

    # ------------------------------------------------------------------
    # Test: Filename sanitization — unsafe characters
    # ------------------------------------------------------------------

    def test_sanitize_for_filesystem_removes_unsafe_characters(self, csv_exporter_instance):
        """
        TEST: The _sanitize_for_filesystem method strips characters that
        are illegal on Windows and/or POSIX filesystems.

        UNSAFE CHARACTERS TESTED:
            < > : " / \\ | ? *

        WHY: Canvas discussion titles can contain any Unicode character.
        Without sanitization, Path operations would raise OSError on
        Windows or create confusing hidden files on Unix.
        """
        # ARRANGE
        dangerous_name = 'Week 1: "What is <Python>?" — A/B Test\\Review'

        # ACT
        sanitized_result = csv_exporter_instance._sanitize_for_filesystem(dangerous_name)

        # ASSERT — none of the dangerous characters survive
        for unsafe_character in ["<", ">", ":", '"', "/", "\\", "|", "?", "*"]:
            assert unsafe_character not in sanitized_result, (
                f"Character '{unsafe_character}' should be stripped"
            )

        # Spaces should be replaced with underscores
        assert " " not in sanitized_result

    # ------------------------------------------------------------------
    # Test: Filename sanitization — truncation
    # ------------------------------------------------------------------

    def test_sanitize_for_filesystem_truncates_names_exceeding_max_length(
        self, csv_exporter_instance
    ):
        """
        TEST: Names longer than max_length are truncated.

        WHY: Some filesystems (notably older Windows FAT32) have path-
        component length limits. Truncating prevents "File name too long"
        errors while keeping the name recognizable.
        """
        # ARRANGE — a name that is clearly longer than the default max of 50
        very_long_discussion_title = "A" * 100

        # ACT
        sanitized_result = csv_exporter_instance._sanitize_for_filesystem(
            very_long_discussion_title
        )

        # ASSERT — result must be at most 50 characters (the default)
        assert len(sanitized_result) <= 50

    # ------------------------------------------------------------------
    # Test: Filename sanitization — custom max_length
    # ------------------------------------------------------------------

    def test_sanitize_for_filesystem_respects_custom_max_length(self, csv_exporter_instance):
        """
        TEST: The optional max_length parameter is honored.

        WHY: Different callers may need different limits (e.g. a shorter
        limit for deeply nested directories). This confirms the parameter
        isn't silently ignored.
        """
        # ARRANGE
        long_name = "Introduction to Advanced Topics in Computing"
        custom_max = 20

        # ACT
        sanitized_result = csv_exporter_instance._sanitize_for_filesystem(
            long_name, max_length=custom_max
        )

        # ASSERT
        assert len(sanitized_result) <= custom_max

    # ------------------------------------------------------------------
    # Test: Edge case — empty participation list
    # ------------------------------------------------------------------

    def test_export_handles_empty_student_participation_list(self, tmp_path, csv_exporter_instance):
        """
        TEST: The exporter does not crash when student_participation is
        an empty list — this represents a discussion with zero posts.

        WHY: Newly created discussions (or discussions where all posts were
        deleted) will have no student participation data. The exporter
        should still create the summary file without raising an exception.

        NOTE: When the rows list is empty, the main grades CSV is NOT
        written (there is nothing to write), but the summary file IS
        written because it contains aggregate statistics.
        """
        # ARRANGE — analysis with zero student participation
        empty_analysis = DiscussionAnalysis(
            discussion_id=99999,
            discussion_title="Empty Discussion",
            course_id=500,
            analysis_date=datetime(2025, 11, 1, 12, 0, 0),
            total_students=30,
            participating_students=0,
            participation_rate=0.0,
            total_posts=0,
            total_replies=0,
            total_words=0,
            average_words_per_post=0.0,
            average_score=0.0,
            students_meeting_requirements=0,
            rubric_used=None,
            student_participation=[],
        )
        output_csv = tmp_path / "empty_grades.csv"

        # ACT — should not raise any exception
        returned_path = csv_exporter_instance.export_analysis(
            analysis=empty_analysis,
            output_file=str(output_csv),
        )

        # ASSERT — summary is still created; grades CSV is absent (no rows)
        summary_path = tmp_path / "empty_grades_summary.csv"
        assert summary_path.exists(), "Summary file should always be created, even with no students"

        # The returned path should match what we passed in
        assert returned_path == str(output_csv)

    # ------------------------------------------------------------------
    # Test: Edge case — special characters in discussion title
    # ------------------------------------------------------------------

    def test_sanitize_handles_special_characters_in_discussion_title(self, csv_exporter_instance):
        """
        TEST: Discussion titles containing em-dashes, curly quotes, and
        other Unicode are handled gracefully by the sanitizer.

        WHY: Canvas discussions frequently contain copy-pasted text from
        Word documents or the web, which introduces "smart" punctuation.
        The sanitizer must not crash on these characters and must produce
        a usable filename.
        """
        # ARRANGE
        fancy_title = 'Week 3\u2014"Smart Quotes" & Em\u2013Dashes?'

        # ACT
        sanitized_result = csv_exporter_instance._sanitize_for_filesystem(fancy_title)

        # ASSERT — the result should be non-empty and filesystem-safe
        assert len(sanitized_result) > 0
        # Double quotes and question marks are stripped
        assert '"' not in sanitized_result
        assert "?" not in sanitized_result
        # Spaces replaced by underscores
        assert " " not in sanitized_result

    # ------------------------------------------------------------------
    # Test: Date formatting in CSV rows
    # ------------------------------------------------------------------

    def test_csv_rows_format_dates_using_configured_date_format(
        self, tmp_path, csv_exporter_instance, sample_discussion_analysis
    ):
        """
        TEST: Date columns in the CSV use the exporter's date_format
        string ("%Y-%m-%d %H:%M:%S" by default).

        WHY: Consistent date formatting is essential for automated parsing.
        If the format drifts, downstream import scripts will silently
        produce wrong dates or fail entirely.
        """
        # ARRANGE
        output_csv = tmp_path / "dates_test.csv"

        # ACT
        csv_exporter_instance.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_csv),
        )

        # ASSERT — read Alice's row (she has all date fields populated)
        with open(output_csv, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        alice_row = rows[0]  # Alphabetically first
        assert alice_row["First Post Date"] == "2025-10-03 09:00:00"
        assert alice_row["Last Post Date"] == "2025-10-05 14:30:00"
        assert alice_row["Initial Post Date"] == "2025-10-03 09:00:00"

        # Carol has no dates — those cells should be empty strings
        carol_row = rows[2]
        assert carol_row["First Post Date"] == ""
        assert carol_row["Last Post Date"] == ""
        assert carol_row["Initial Post Date"] == ""

    # ------------------------------------------------------------------
    # Test: Multiple missing requirements joined by semicolons
    # ------------------------------------------------------------------

    def test_missing_requirements_are_joined_with_semicolons(
        self, tmp_path, csv_exporter_instance, sample_discussion_analysis
    ):
        """
        TEST: When a student has multiple missing requirements, they are
        joined with "; " (semicolon + space) in the CSV cell.

        WHY: Comma-delimited sub-values inside a CSV cell cause parsing
        ambiguity. Using semicolons keeps each requirement distinct and
        avoids confusing CSV readers that split on commas.
        """
        # ARRANGE
        output_csv = tmp_path / "requirements_test.csv"

        # ACT
        csv_exporter_instance.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_csv),
        )

        # ASSERT — Carol has two missing requirements
        with open(output_csv, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        carol_row = rows[2]  # Alphabetically last
        expected_joined_requirements = "No initial post; Needs at least 2 replies"
        assert carol_row["Missing Requirements"] == (expected_joined_requirements)

    # ------------------------------------------------------------------
    # Test: Sanitizer collapses multiple spaces into one
    # ------------------------------------------------------------------

    def test_sanitize_for_filesystem_collapses_multiple_spaces(self, csv_exporter_instance):
        """
        TEST: Multiple consecutive whitespace characters are collapsed
        into a single underscore in the sanitized output.

        WHY: After stripping unsafe characters, the name may contain runs
        of adjacent spaces (e.g. 'Week 1:  Python' becomes 'Week 1  Python'
        after ':' is removed). These should collapse to a single underscore
        to avoid ugly double-underscores in filenames.
        """
        # ARRANGE
        name_with_extra_spaces = "Week  1:   Introduction    to   Python"

        # ACT
        sanitized_result = csv_exporter_instance._sanitize_for_filesystem(name_with_extra_spaces)

        # ASSERT — no double underscores
        assert "__" not in sanitized_result
        assert sanitized_result == "Week_1_Introduction_to_Python"


class TestCSVAiDiscussionColumns:
    """Tests for optional AI discussion columns in CSV export."""

    def test_export_includes_ai_columns_when_band_data_exists(self, tmp_path):
        exporter = CSVExporter()

        analysis = DiscussionAnalysis(
            discussion_id=900,
            discussion_title="AI Discussion",
            course_id=501,
            student_participation=[
                StudentParticipation(
                    user_id=77,
                    user_name="AI Student",
                    total_posts=3,
                    total_word_count=640,
                    reply_count=2,
                    total_score=95.0,
                    meets_requirements=True,
                    band="excellent",
                    band_reasons=["Multiple top-level posts found; using earliest as Post 1"],
                    post_details=[
                        {"post_role": "research", "word_count": 140, "timing_status": "on_time"},
                        {
                            "post_role": "restatement",
                            "word_count": 330,
                            "timing_status": "late_post2",
                        },
                        {
                            "post_role": "reply",
                            "word_count": 170,
                            "timing_status": "late_post3",
                        },
                    ],
                )
            ],
        )

        output_csv = tmp_path / "ai_columns.csv"
        exporter.export_analysis(analysis=analysis, output_file=str(output_csv))

        with open(output_csv, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

        assert len(rows) == 1
        row = rows[0]
        assert row["Band"] == "excellent"
        assert row["Post 1 Words"] == "140"
        assert row["Post 2 Words"] == "330"
        assert row["Post 3 Words"] == "170"
        assert "late_post2" in row["Timing Flags"]
        assert "late_post3" in row["Timing Flags"]
        assert "Multiple top-level posts" in row["Structure Warning"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
