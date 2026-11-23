#!/usr/bin/env python3
"""
CSV Exporter
============
Export discussion analysis results to CSV format.

"""

import csv
from datetime import datetime
from pathlib import Path

from coursewright.core.canvas.models import DiscussionAnalysis
from coursewright.core.export._destination import (
    default_export_base_directory,
    sanitize_name_for_filesystem,
)


class CSVExporter:
    """Exports discussion analysis to CSV format"""

    def __init__(self) -> None:
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
        Export discussion analysis to CSV file using standardized folder structure.

        Args:
            analysis: DiscussionAnalysis object
            output_file: Optional custom output file path. If None, uses standard structure.
            course_name: Optional human-readable course name (e.g., "COURSE101.0001.F25")

        Returns:
            Path to the created CSV file
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
            output_path = discussion_dir / f"grades_{timestamp}.csv"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Prepare data for CSV
        rows = []
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

            rows.append(row)

        # Sort by student name
        rows.sort(key=lambda x: x["Student Name"])

        # Write CSV
        if rows:
            fieldnames = rows[0].keys()

            with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

        # Create summary file
        summary_file = output_path.with_name(f"{output_path.stem}_summary.csv")
        self._export_summary(analysis, summary_file)

        return str(output_path)

    def _export_summary(self, analysis: DiscussionAnalysis, output_file: str) -> None:
        """Export summary statistics to separate CSV"""
        summary_data = [
            ["Metric", "Value"],
            ["Discussion Title", analysis.discussion_title],
            ["Discussion ID", analysis.discussion_id],
            ["Course ID", analysis.course_id],
            ["Analysis Date", analysis.analysis_date.strftime(self.date_format)],
            ["Total Students", analysis.total_students],
            ["Participating Students", analysis.participating_students],
            ["Participation Rate (%)", round(analysis.participation_rate, 2)],
            ["Total Posts", analysis.total_posts],
            ["Total Replies", analysis.total_replies],
            ["Total Words", analysis.total_words],
            ["Average Words per Post", round(analysis.average_words_per_post, 2)],
            ["Average Score", round(analysis.average_score, 2)],
            ["Students Meeting Requirements", analysis.students_meeting_requirements],
            ["Rubric Used", analysis.rubric_used or "Default"],
        ]

        with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerows(summary_data)
