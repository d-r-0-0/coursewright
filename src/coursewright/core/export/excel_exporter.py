#!/usr/bin/env python3
"""
Excel Exporter
==============
Export discussion analysis results to Excel format with formatting.

"""

from datetime import datetime
from pathlib import Path
from typing import Any

from coursewright.core.canvas.models import DiscussionAnalysis
from coursewright.core.export._destination import (
    default_export_base_directory,
    sanitize_name_for_filesystem,
)

try:
    import openpyxl  # noqa: F401 — availability check
    import pandas as pd
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils.dataframe import dataframe_to_rows  # noqa: F401

    EXCEL_AVAILABLE = True
except ImportError:
    EXCEL_AVAILABLE = False


class ExcelExporter:
    """Exports discussion analysis to Excel format with formatting"""

    def __init__(self) -> None:
        if not EXCEL_AVAILABLE:
            raise ImportError(
                "Excel export requires pandas and openpyxl. "
                "Install with: pip install pandas openpyxl"
            )

        self.date_format = "%Y-%m-%d %H:%M:%S"

    def _sanitize_for_filesystem(self, name: str, max_length: int = 50) -> str:
        """Sanitize a name to be filesystem-safe (shared helper)."""
        return sanitize_name_for_filesystem(name, max_length)

    def export_analysis(
        self,
        analysis: DiscussionAnalysis,
        output_file: str | None = None,
        course_name: str | None = None,
    ) -> str:
        """
        Export discussion analysis to Excel file with multiple sheets and formatting.
        Uses standardized folder structure: <workspace>/exports/[CourseCode]/[DiscussionName]/

        Args:
            analysis: DiscussionAnalysis object
            output_file: Optional custom output file path. If None, uses standard structure.
            course_name: Optional human-readable course name (e.g., "COURSE101.0001.F25")

        Returns:
            Path to the created Excel file
        """
        # Create standardized output directory structure
        if output_file:
            output_path = Path(output_file)
        else:
            base_path = default_export_base_directory()

            # Use human-readable names
            if course_name:
                course_dir_name = self._sanitize_for_filesystem(course_name)
            else:
                course_dir_name = f"course_{analysis.course_id}"

            discussion_name = self._sanitize_for_filesystem(analysis.discussion_title)
            discussion_dir = base_path / course_dir_name / discussion_name
            discussion_dir.mkdir(parents=True, exist_ok=True)

            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = discussion_dir / f"grades_{timestamp}.xlsx"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Create Excel writer
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            # Export main data
            self._export_student_data(analysis, writer)

            # Export summary
            self._export_summary_sheet(analysis, writer)

            # Export statistics
            self._export_statistics_sheet(analysis, writer)

            # Apply formatting
            self._apply_formatting(writer.book)

        return str(output_path)

    def _export_student_data(self, analysis: DiscussionAnalysis, writer: Any) -> None:
        """Export individual student data to main sheet"""
        # Prepare data
        data = []
        has_ai_profile_data = any(
            getattr(student, "band", None) for student in analysis.student_participation
        )

        for student in analysis.student_participation:
            role_word_counts = {"research": 0, "restatement": 0, "reply": 0}
            timing_flags = set()

            if has_ai_profile_data:
                for post_detail in student.post_details:
                    role = post_detail.get("post_role")
                    if role in role_word_counts and role_word_counts[role] == 0:
                        role_word_counts[role] = post_detail.get("word_count", 0)

                    timing_status = post_detail.get("timing_status")
                    if timing_status in {"late_post2", "late_post3", "unknown"}:
                        timing_flags.add(timing_status)

            structure_warning = ""
            if has_ai_profile_data and student.band_reasons:
                warnings = [
                    reason
                    for reason in student.band_reasons
                    if "Parent post missing" in reason
                    or "Multiple top-level" in reason
                    or "Reply-to-self" in reason
                ]
                structure_warning = "; ".join(warnings)

            row = {
                "Student Name": student.user_name,
                "User ID": student.user_id,
                "Total Posts": student.total_posts,
                "Initial Post": "Yes" if student.initial_post else "No",
                "Initial Post Words": student.initial_post_word_count,
                "Reply Count": student.reply_count,
                "Total Words": student.total_word_count,
                "Participation Score": round(student.participation_score, 2),
                "Quality Score": round(student.quality_score, 2),
                "Total Score": round(student.total_score, 2),
                "Meets Requirements": "Yes" if student.meets_requirements else "No",
                "Missing Requirements": "; ".join(student.missing_requirements),
                "First Post Date": student.first_post_date.strftime(self.date_format)
                if student.first_post_date
                else "",
                "Last Post Date": student.last_post_date.strftime(self.date_format)
                if student.last_post_date
                else "",
                "Initial Post Date": student.initial_post_date.strftime(self.date_format)
                if student.initial_post_date
                else "",
            }

            if has_ai_profile_data:
                row.update(
                    {
                        "Post 1 Words": role_word_counts["research"],
                        "Post 2 Words": role_word_counts["restatement"],
                        "Post 3 Words": role_word_counts["reply"],
                        "Band": student.band or "",
                        "Timing Flags": ", ".join(sorted(timing_flags)),
                        "Structure Warning": structure_warning,
                    }
                )

            data.append(row)

        # Create DataFrame
        df = pd.DataFrame(data)
        df = df.sort_values("Student Name")

        # Write to Excel
        df.to_excel(writer, sheet_name="Student Results", index=False)

    def _export_summary_sheet(self, analysis: DiscussionAnalysis, writer: Any) -> None:
        """Export discussion summary to separate sheet"""
        summary_data = {
            "Discussion Information": [
                ["Discussion Title", analysis.discussion_title],
                ["Discussion ID", analysis.discussion_id],
                ["Course ID", analysis.course_id],
                ["Analysis Date", analysis.analysis_date.strftime(self.date_format)],
                ["Rubric Used", analysis.rubric_used or "Default"],
                ["", ""],  # Spacer
            ],
            "Participation Statistics": [
                ["Total Students", analysis.total_students],
                ["Participating Students", analysis.participating_students],
                ["Participation Rate (%)", round(analysis.participation_rate, 2)],
                [
                    "Students Meeting Requirements",
                    f"{analysis.students_meeting_requirements}/{analysis.total_students}",
                ],
                ["", ""],  # Spacer
            ],
            "Content Statistics": [
                ["Total Posts", analysis.total_posts],
                ["Total Replies", analysis.total_replies],
                ["Total Words", analysis.total_words],
                ["Average Words per Post", round(analysis.average_words_per_post, 2)],
                ["", ""],  # Spacer
            ],
            "Grading Statistics": [
                ["Average Score", round(analysis.average_score, 2)],
                [
                    "Highest Score",
                    round(
                        max((s.total_score for s in analysis.student_participation), default=0), 2
                    ),
                ],
                [
                    "Lowest Score",
                    round(
                        min((s.total_score for s in analysis.student_participation), default=0), 2
                    ),
                ],
            ],
        }

        # Create summary DataFrame
        all_rows = []
        for section, rows in summary_data.items():
            all_rows.append([section, ""])  # Section header
            all_rows.extend(rows)
            all_rows.append(["", ""])  # Spacer

        summary_df = pd.DataFrame(all_rows, columns=["Metric", "Value"])
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

    def _export_statistics_sheet(self, analysis: DiscussionAnalysis, writer: Any) -> None:
        """Export detailed statistics to separate sheet"""
        # Post distribution
        post_counts = {}
        word_counts = {}

        for student in analysis.student_participation:
            posts = student.total_posts
            words = student.total_word_count

            post_counts[posts] = post_counts.get(posts, 0) + 1

            # Group word counts in ranges
            word_range = f"{(words // 50) * 50}-{(words // 50) * 50 + 49}"
            word_counts[word_range] = word_counts.get(word_range, 0) + 1

        # Create statistics data
        stats_data = []

        # Post distribution
        stats_data.append(["Post Count Distribution", ""])
        for posts, count in sorted(post_counts.items()):
            stats_data.append([f"{posts} posts", count])
        stats_data.append(["", ""])

        # Word count distribution
        stats_data.append(["Word Count Distribution", ""])
        for word_range, count in sorted(word_counts.items()):
            stats_data.append([f"{word_range} words", count])
        stats_data.append(["", ""])

        # Requirements compliance
        stats_data.append(["Requirements Compliance", ""])
        meeting_req = len([s for s in analysis.student_participation if s.meets_requirements])
        not_meeting_req = len(analysis.student_participation) - meeting_req
        stats_data.append(["Meeting Requirements", meeting_req])
        stats_data.append(["Not Meeting Requirements", not_meeting_req])

        stats_df = pd.DataFrame(stats_data, columns=["Category", "Count"])
        stats_df.to_excel(writer, sheet_name="Statistics", index=False)

    def _apply_formatting(self, workbook: Any) -> None:
        """Apply formatting to Excel workbook"""
        # Define styles
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")

        section_font = Font(bold=True, size=12)
        section_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")

        border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
        )

        # Format each sheet
        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]

            # Auto-adjust column widths
            for column in sheet.columns:
                max_length = 0
                column_letter = column[0].column_letter

                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except (TypeError, AttributeError):
                        pass

                adjusted_width = min(max_length + 2, 50)  # Cap at 50
                sheet.column_dimensions[column_letter].width = adjusted_width

            # Format headers
            if sheet.max_row > 0:
                for cell in sheet[1]:
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.border = border

            # Format data cells
            for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row):
                for cell in row:
                    cell.border = border
                    cell.alignment = Alignment(vertical="center")

            # Special formatting for Summary sheet
            if sheet_name == "Summary":
                for row_num in range(1, sheet.max_row + 1):
                    cell = sheet[f"A{row_num}"]
                    if (
                        cell.value
                        and not str(cell.value).startswith(" ")
                        and any(
                            section in str(cell.value) for section in ["Information", "Statistics"]
                        )
                    ):
                        cell.font = section_font
                        cell.fill = section_fill
                        # Apply to entire row
                        for col in range(1, sheet.max_column + 1):
                            sheet.cell(row_num, col).font = section_font
                            sheet.cell(row_num, col).fill = section_fill
