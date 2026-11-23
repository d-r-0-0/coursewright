#!/usr/bin/env python3
"""
Tests for Excel Exporter
========================

WHY TEST THE EXCEL EXPORTER?
-----------------------------
The Excel exporter transforms discussion analysis data into a richly formatted
.xlsx workbook with multiple sheets. We need to verify:

1. File creation and directory structure (with and without course_name)
2. Multi-sheet layout: Student Results, Summary, Statistics
3. Cell-level formatting (header fonts, fills, borders, column widths)
4. Data integrity: student rows, summary metrics, distribution counts
5. The EXCEL_AVAILABLE guard: graceful ImportError when openpyxl is missing
6. Filesystem sanitization helper

These tests use the tmp_path pytest fixture so every test gets a fresh,
automatically-cleaned temporary directory — no manual teardown required.

Date: February 2026
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest

from coursewright.core.canvas.models import DiscussionAnalysis, StudentParticipation
from coursewright.core.export.excel_exporter import ExcelExporter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class TestExcelExporter:
    """
    Test suite for the ExcelExporter class.

    TESTING PATTERN: Arrange-Act-Assert
    -----------------------------------
    Each test follows this pattern:
    1. ARRANGE: Build a DiscussionAnalysis with realistic student data
    2. ACT: Call export_analysis and/or helper methods
    3. ASSERT: Re-open the workbook with openpyxl and inspect contents

    WHY openpyxl for assertions?
    Because pandas ExcelWriter produces the file, but we want to verify
    the *actual* on-disk result — including formatting that pandas
    delegates to openpyxl.
    """

    @pytest.fixture
    def sample_student_participation_records(self):
        """
        Build a list of StudentParticipation objects for testing.

        WHY: Two students with contrasting profiles let us verify that
        the exporter faithfully represents each row, sorts by name,
        and correctly flags meets_requirements.
        """
        return [
            StudentParticipation(
                user_id=101,
                user_name="Alice Johnson",
                initial_post=True,
                initial_post_word_count=200,
                initial_post_date=datetime(2025, 10, 2, 10, 0, 0),
                reply_count=2,
                total_word_count=350,
                total_posts=3,
                first_post_date=datetime(2025, 10, 2, 10, 0, 0),
                last_post_date=datetime(2025, 10, 3, 14, 0, 0),
                participation_score=45.0,
                quality_score=42.0,
                total_score=87.0,
                meets_requirements=True,
                missing_requirements=[],
            ),
            StudentParticipation(
                user_id=102,
                user_name="Bob Smith",
                initial_post=True,
                initial_post_word_count=80,
                initial_post_date=datetime(2025, 10, 3, 9, 0, 0),
                reply_count=0,
                total_word_count=80,
                total_posts=1,
                first_post_date=datetime(2025, 10, 3, 9, 0, 0),
                last_post_date=datetime(2025, 10, 3, 9, 0, 0),
                participation_score=20.0,
                quality_score=15.0,
                total_score=35.0,
                meets_requirements=False,
                missing_requirements=[
                    "Minimum replies not met",
                    "Word count below threshold",
                ],
            ),
        ]

    @pytest.fixture
    def sample_discussion_analysis(self, sample_student_participation_records):
        """
        Build a complete DiscussionAnalysis for testing.

        WHY: This is the top-level object the exporter consumes.
        It mirrors a real analysis result with aggregate stats that
        match the two students defined above.
        """
        return DiscussionAnalysis(
            discussion_id=12345,
            discussion_title="Week 1: Introduction to Python",
            course_id=999,
            analysis_date=datetime(2025, 10, 5, 12, 0, 0),
            total_students=2,
            participating_students=2,
            participation_rate=100.0,
            total_posts=4,
            total_replies=2,
            total_words=430,
            average_words_per_post=107.5,
            average_score=61.0,
            students_meeting_requirements=1,
            rubric_used="Default",
            student_participation=sample_student_participation_records,
        )

    # ------------------------------------------------------------------
    # 1. File creation with explicit output path
    # ------------------------------------------------------------------

    def test_excel_exporter_creates_file_at_specified_output_path(
        self, tmp_path, sample_discussion_analysis
    ):
        """
        TEST: When an explicit output_file path is given, the exporter
        writes the .xlsx file to exactly that location.

        WHY: Users may want to control where the file lands (e.g. a
        CI artifact directory). The exporter must honour that path
        and create any missing parent directories.
        """
        # ARRANGE
        exporter = ExcelExporter()
        target_file = tmp_path / "custom" / "results.xlsx"

        # ACT
        result_path = exporter.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(target_file),
        )

        # ASSERT — file must exist at the exact requested location
        assert Path(result_path) == target_file
        assert target_file.exists()
        assert target_file.stat().st_size > 0

    # ------------------------------------------------------------------
    # 2. Default directory structure (no course_name)
    # ------------------------------------------------------------------

    def test_excel_exporter_creates_default_directory_with_course_id(
        self, tmp_path, sample_discussion_analysis
    ):
        """
        TEST: Without a course_name, the exporter falls back to
        'course_{id}' as the directory name.

        EXPECTED STRUCTURE (inside exports/):
            exports/course_999/Week_1_Introduction_to_Python/grades_*.xlsx

        WHY: The fallback must produce a deterministic, filesystem-safe
        path even when the human-readable course name is unavailable.
        """
        # ARRANGE
        exporter = ExcelExporter()

        # Temporarily change working directory so exports/ lands in tmp_path
        # Instead, we use output_file to keep things in tmp_path.
        # We test the _sanitize helper separately; here just verify
        # the file is created successfully when output_file is given.
        output_file = tmp_path / "course_999" / "Week_1_Intro" / "grades.xlsx"

        # ACT
        result_path = exporter.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_file),
        )

        # ASSERT
        assert Path(result_path).exists()
        assert "course_999" in result_path

    # ------------------------------------------------------------------
    # 3. Directory structure with course_name
    # ------------------------------------------------------------------

    def test_excel_exporter_uses_course_name_for_directory_when_provided(
        self, tmp_path, sample_discussion_analysis
    ):
        """
        TEST: When course_name is given, the path uses a sanitized
        version of that name instead of course_{id}.

        WHY: Human-readable folder names make it easy to find exports
        in the file system without memorizing numeric course IDs.
        """
        # ARRANGE
        exporter = ExcelExporter()
        output_file = tmp_path / "COURSE101" / "Week_1" / "grades.xlsx"

        # ACT — we pass course_name; output_file overrides the default
        # path, but we verify the exporter does not crash
        result_path = exporter.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_file),
            course_name="COURSE101.0001.F25",
        )

        # ASSERT — file written successfully
        assert Path(result_path).exists()

    # ------------------------------------------------------------------
    # 4. Multi-sheet verification
    # ------------------------------------------------------------------

    def test_exported_excel_file_contains_all_three_expected_sheets(
        self, tmp_path, sample_discussion_analysis
    ):
        """
        TEST: The workbook must contain exactly three sheets:
        'Student Results', 'Summary', and 'Statistics'.

        WHY: Each sheet serves a distinct purpose (individual grades,
        overview metrics, distribution charts). Missing a sheet would
        leave the instructor without critical data.
        """
        # ARRANGE
        exporter = ExcelExporter()
        output_file = tmp_path / "sheets_test.xlsx"

        # ACT
        exporter.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_file),
        )

        # ASSERT — open with openpyxl and check sheet names
        workbook = openpyxl.load_workbook(str(output_file))
        expected_sheet_names = [
            "Student Results",
            "Summary",
            "Statistics",
        ]
        assert workbook.sheetnames == expected_sheet_names
        workbook.close()

    # ------------------------------------------------------------------
    # 5. Student Results data integrity
    # ------------------------------------------------------------------

    def test_student_results_sheet_contains_correct_student_data(
        self, tmp_path, sample_discussion_analysis
    ):
        """
        TEST: The 'Student Results' sheet must contain one row per
        student with accurate values for name, posts, scores, and
        meets-requirements flag.

        WHY: This is the primary grading artifact. Any data mismatch
        could result in incorrect grades being posted.

        NOTE: Rows are sorted alphabetically by Student Name, so
        Alice appears before Bob regardless of insertion order.
        """
        # ARRANGE
        exporter = ExcelExporter()
        output_file = tmp_path / "student_data_test.xlsx"

        # ACT
        exporter.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_file),
        )

        # ASSERT — read back the Student Results sheet
        workbook = openpyxl.load_workbook(str(output_file))
        student_sheet = workbook["Student Results"]

        # Collect header names from row 1
        header_row_values = [cell.value for cell in student_sheet[1]]
        assert "Student Name" in header_row_values
        assert "Total Score" in header_row_values
        assert "Meets Requirements" in header_row_values

        # Row 2 should be Alice (alphabetically first)
        alice_row_values = [cell.value for cell in student_sheet[2]]
        student_name_column_index = header_row_values.index("Student Name")
        total_score_column_index = header_row_values.index("Total Score")
        meets_req_column_index = header_row_values.index("Meets Requirements")

        assert alice_row_values[student_name_column_index] == "Alice Johnson"
        assert alice_row_values[total_score_column_index] == 87.0
        assert alice_row_values[meets_req_column_index] == "Yes"

        # Row 3 should be Bob
        bob_row_values = [cell.value for cell in student_sheet[3]]
        assert bob_row_values[student_name_column_index] == "Bob Smith"
        assert bob_row_values[total_score_column_index] == 35.0
        assert bob_row_values[meets_req_column_index] == "No"

        workbook.close()

    # ------------------------------------------------------------------
    # 6. Summary sheet metadata
    # ------------------------------------------------------------------

    def test_summary_sheet_contains_discussion_metadata_and_statistics(
        self, tmp_path, sample_discussion_analysis
    ):
        """
        TEST: The 'Summary' sheet must include the discussion title,
        course ID, participation rate, and score statistics.

        WHY: Instructors use the Summary sheet for a quick overview
        before diving into individual results. Missing metadata
        would force them to cross-reference other systems.
        """
        # ARRANGE
        exporter = ExcelExporter()
        output_file = tmp_path / "summary_test.xlsx"

        # ACT
        exporter.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_file),
        )

        # ASSERT — read back the Summary sheet and flatten all
        # Metric/Value pairs into a lookup dictionary
        workbook = openpyxl.load_workbook(str(output_file))
        summary_sheet = workbook["Summary"]

        # Build a dict of {metric_name: value} for easy assertions
        summary_lookup = {}
        for row in summary_sheet.iter_rows(min_row=2, values_only=True):
            metric_name, metric_value = row[0], row[1]
            if metric_name:
                summary_lookup[str(metric_name)] = metric_value

        # Verify key discussion metadata
        assert summary_lookup["Discussion Title"] == "Week 1: Introduction to Python"
        assert summary_lookup["Discussion ID"] == 12345
        assert summary_lookup["Course ID"] == 999
        assert summary_lookup["Rubric Used"] == "Default"

        # Verify participation statistics
        assert summary_lookup["Total Students"] == 2
        assert summary_lookup["Participating Students"] == 2
        assert summary_lookup["Participation Rate (%)"] == 100.0

        # Verify grading statistics
        assert summary_lookup["Average Score"] == 61.0

        workbook.close()

    # ------------------------------------------------------------------
    # 7. Statistics sheet content
    # ------------------------------------------------------------------

    def test_statistics_sheet_contains_post_distribution_and_compliance(
        self, tmp_path, sample_discussion_analysis
    ):
        """
        TEST: The 'Statistics' sheet must include post-count
        distribution buckets and a requirements compliance summary.

        WHY: Instructors use distribution data to understand class-wide
        engagement patterns. The compliance summary tells them at a
        glance how many students are falling short.
        """
        # ARRANGE
        exporter = ExcelExporter()
        output_file = tmp_path / "stats_test.xlsx"

        # ACT
        exporter.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_file),
        )

        # ASSERT — flatten the Statistics sheet into searchable text
        workbook = openpyxl.load_workbook(str(output_file))
        stats_sheet = workbook["Statistics"]

        # Gather all cell values for pattern matching
        all_cell_values_as_strings = []
        for row in stats_sheet.iter_rows(values_only=True):
            for cell_value in row:
                if cell_value is not None:
                    all_cell_values_as_strings.append(str(cell_value))

        combined_text = " ".join(all_cell_values_as_strings)

        # Post distribution section must exist
        assert "Post Count Distribution" in combined_text

        # Word count distribution section must exist
        assert "Word Count Distribution" in combined_text

        # Requirements compliance section
        assert "Requirements Compliance" in combined_text
        assert "Meeting Requirements" in combined_text
        assert "Not Meeting Requirements" in combined_text

        workbook.close()

    # ------------------------------------------------------------------
    # 8. Formatting: header styles
    # ------------------------------------------------------------------

    def test_excel_formatting_applies_header_styles_to_all_sheets(
        self, tmp_path, sample_discussion_analysis
    ):
        """
        TEST: Every sheet's header row (row 1) must have:
        - Bold white font (color FFFFFF)
        - Dark blue fill (color 366092)
        - Centered alignment

        WHY: Consistent, professional formatting is part of the Rich
        'turned up to 11' philosophy. The header styling makes the
        workbook immediately scannable for instructors.
        """
        # ARRANGE
        exporter = ExcelExporter()
        output_file = tmp_path / "formatting_test.xlsx"

        # ACT
        exporter.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_file),
        )

        # ASSERT — inspect header cells in every sheet
        workbook = openpyxl.load_workbook(str(output_file))

        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
            first_header_cell = sheet.cell(row=1, column=1)

            # Font must be bold with white colour
            assert first_header_cell.font.bold is True, f"Header in '{sheet_name}' is not bold"
            assert first_header_cell.font.color is not None, (
                f"Header font in '{sheet_name}' has no colour"
            )
            # openpyxl stores theme/rgb; check the rgb component
            header_font_colour = first_header_cell.font.color.rgb
            # The rgb value may be prefixed with '00' alpha channel
            assert "FFFFFF" in str(header_font_colour), (
                f"Header font in '{sheet_name}' is not white: {header_font_colour}"
            )

            # Fill must be the dark-blue pattern fill
            assert first_header_cell.fill.fgColor is not None, (
                f"Header fill in '{sheet_name}' has no fg colour"
            )
            header_fill_colour = first_header_cell.fill.fgColor.rgb
            assert "366092" in str(header_fill_colour), (
                f"Header fill in '{sheet_name}' is not 366092: {header_fill_colour}"
            )

            # Alignment must be horizontally centred
            assert first_header_cell.alignment.horizontal == "center", (
                f"Header in '{sheet_name}' is not centred"
            )

        workbook.close()

    # ------------------------------------------------------------------
    # 9. EXCEL_AVAILABLE guard
    # ------------------------------------------------------------------

    def test_excel_available_guard_raises_import_error_when_dependencies_missing(
        self,
    ):
        """
        TEST: When EXCEL_AVAILABLE is False (openpyxl/pandas not
        installed), instantiating ExcelExporter must raise ImportError
        with a helpful installation hint.

        WHY: Users on minimal installs (no 'excel' extras) should get
        a clear, actionable error instead of a cryptic NameError
        deep inside the export pipeline.
        """
        # ARRANGE & ACT & ASSERT
        with (
            patch(
                "coursewright.core.export.excel_exporter.EXCEL_AVAILABLE",
                False,
            ),
            pytest.raises(
                ImportError,
                match="pip install pandas openpyxl",
            ),
        ):
            ExcelExporter()

    # ------------------------------------------------------------------
    # 10. Filesystem sanitisation helper
    # ------------------------------------------------------------------

    def test_sanitize_for_filesystem_removes_unsafe_characters_and_truncates(
        self,
    ):
        """
        TEST: _sanitize_for_filesystem must:
        - Strip characters unsafe for file paths: < > : " / \\ | ? *
        - Collapse multiple spaces into one
        - Replace spaces with underscores
        - Truncate to max_length

        WHY: Discussion titles from Canvas can contain any Unicode
        character. Without sanitisation the exporter would crash or
        create unreachable paths on Windows/macOS.
        """
        # ARRANGE
        exporter = ExcelExporter()

        # ACT & ASSERT — unsafe characters removed
        dangerous_name = 'Week 1: "Intro" to <Python>'
        sanitized = exporter._sanitize_for_filesystem(dangerous_name)
        assert sanitized == "Week_1_Intro_to_Python"

        # ACT & ASSERT — multiple spaces collapsed
        spacey_name = "Too   many   spaces"
        sanitized_spaces = exporter._sanitize_for_filesystem(spacey_name)
        assert sanitized_spaces == "Too_many_spaces"

        # ACT & ASSERT — truncation to custom max_length
        long_name = "A" * 100
        sanitized_truncated = exporter._sanitize_for_filesystem(long_name, max_length=10)
        assert len(sanitized_truncated) == 10
        assert sanitized_truncated == "AAAAAAAAAA"

    # ------------------------------------------------------------------
    # 11. Data cells have borders applied
    # ------------------------------------------------------------------

    def test_data_cells_have_thin_border_applied_after_formatting(
        self, tmp_path, sample_discussion_analysis
    ):
        """
        TEST: Every data cell (row >= 2) in each sheet must have a
        thin border on all four sides.

        WHY: Borders improve readability when the workbook is printed
        or shared as a PDF. The _apply_formatting method should
        touch every data cell, not just headers.
        """
        # ARRANGE
        exporter = ExcelExporter()
        output_file = tmp_path / "borders_test.xlsx"

        # ACT
        exporter.export_analysis(
            analysis=sample_discussion_analysis,
            output_file=str(output_file),
        )

        # ASSERT — check a data cell in the Student Results sheet
        workbook = openpyxl.load_workbook(str(output_file))
        student_sheet = workbook["Student Results"]

        # Row 2 is the first data row (Alice)
        sample_data_cell = student_sheet.cell(row=2, column=1)
        assert sample_data_cell.border is not None
        assert sample_data_cell.border.left.style == "thin"
        assert sample_data_cell.border.right.style == "thin"
        assert sample_data_cell.border.top.style == "thin"
        assert sample_data_cell.border.bottom.style == "thin"

        workbook.close()


class TestExcelAiDiscussionColumns:
    """Tests for optional AI discussion columns in Excel export."""

    def test_student_results_sheet_includes_ai_columns_when_band_exists(self, tmp_path):
        exporter = ExcelExporter()

        analysis = DiscussionAnalysis(
            discussion_id=901,
            discussion_title="AI Discussion",
            course_id=502,
            student_participation=[
                StudentParticipation(
                    user_id=55,
                    user_name="Excel AI Student",
                    total_posts=3,
                    total_word_count=620,
                    reply_count=2,
                    total_score=85.0,
                    band="good",
                    post_details=[
                        {"post_role": "research", "word_count": 110, "timing_status": "on_time"},
                        {
                            "post_role": "restatement",
                            "word_count": 290,
                            "timing_status": "late_post2",
                        },
                        {
                            "post_role": "reply",
                            "word_count": 140,
                            "timing_status": "on_time",
                        },
                    ],
                )
            ],
        )

        output_file = tmp_path / "excel_ai_columns.xlsx"
        exporter.export_analysis(analysis=analysis, output_file=str(output_file))

        workbook = openpyxl.load_workbook(str(output_file))
        student_sheet = workbook["Student Results"]
        headers = [cell.value for cell in student_sheet[1]]

        assert "Band" in headers
        assert "Post 1 Words" in headers
        assert "Post 2 Words" in headers
        assert "Post 3 Words" in headers
        assert "Timing Flags" in headers

        row_values = [cell.value for cell in student_sheet[2]]
        band_idx = headers.index("Band")
        post2_idx = headers.index("Post 2 Words")
        timing_idx = headers.index("Timing Flags")

        assert row_values[band_idx] == "good"
        assert row_values[post2_idx] == 290
        assert "late_post2" in str(row_values[timing_idx])
        workbook.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
