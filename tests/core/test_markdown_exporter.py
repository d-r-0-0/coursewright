#!/usr/bin/env python3
"""
Tests for Markdown Exporter
===========================

WHY TEST THE MARKDOWN EXPORTER?
-------------------------------
The markdown exporter is a critical feature for LLM integration. It transforms
complex discussion data into a clean, structured format. We need to test:

1. File creation and directory structure
2. Markdown formatting and HTML cleaning
3. Threading/hierarchy preservation
4. Metadata inclusion
5. Optional analysis integration

These tests ensure that the exported markdown is:
- Well-formatted and readable
- Contains all necessary information
- Properly structured for LLM consumption
- Handles edge cases (no posts, missing data, etc.)

Date: October 23, 2025
"""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from coursewright.core.canvas.models import (
    CanvasDiscussion,
    CanvasDiscussionEntry,
    DiscussionAnalysis,
    StudentParticipation,
)
from coursewright.core.export.markdown_exporter import MarkdownExporter


class TestMarkdownExporter:
    """
    Test suite for the MarkdownExporter class.

    TESTING PATTERN: Arrange-Act-Assert
    -----------------------------------
    Each test follows this pattern:
    1. ARRANGE: Create test data (discussions, entries, etc.)
    2. ACT: Call the exporter method
    3. ASSERT: Verify the output is correct
    """

    @pytest.fixture
    def temp_export_dir(self):
        """
        Create a temporary directory for export tests.

        WHY: We don't want to clutter the actual exports/ directory with test files.
        This fixture creates a temp dir for each test and cleans it up afterward.
        """
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        # Cleanup after test
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def sample_discussion(self):
        """
        Create a sample discussion for testing.

        WHY: We need consistent test data to verify export behavior.
        This creates a realistic discussion with all relevant fields.
        """
        return CanvasDiscussion(
            id=12345,
            title="Week 1: Introduction to Python",
            message=(
                "<p>Discuss your experience with Python. What do you find most challenging?</p>"
            ),
            posted_at=datetime(2025, 10, 1, 9, 0, 0),
            discussion_type="threaded",
            assignment_id=67890,
            discussion_subentry_count=5,
            course_id=999,
        )

    @pytest.fixture
    def sample_entries(self):
        """
        Create sample discussion entries with threading.

        WHY: Testing the threaded structure is critical. This creates:
        - 2 top-level posts
        - 3 replies to the first post
        - Realistic content with HTML

        This tests the exporter's ability to preserve conversation hierarchy.
        """
        entries = [
            # Top-level post 1
            CanvasDiscussionEntry(
                id=1,
                user_id=101,
                user_name="Alice Johnson",
                discussion_id=12345,
                message=(
                    "<p>I've been using Python for about 6 months now. The most "
                    "challenging part for me is understanding list comprehensions "
                    "and when to use them effectively.</p>"
                ),
                created_at=datetime(2025, 10, 2, 10, 0, 0),
                parent_id=None,
                word_count=0,  # Will be auto-calculated
            ),
            # Reply to post 1
            CanvasDiscussionEntry(
                id=2,
                user_id=102,
                user_name="Bob Smith",
                discussion_id=12345,
                message=(
                    "<p>Great point Alice! I find list comprehensions really useful "
                    "once you get the hang of them. They can make your code much "
                    "more concise.</p>"
                ),
                created_at=datetime(2025, 10, 2, 14, 0, 0),
                parent_id=1,
                word_count=0,
            ),
            # Another reply to post 1
            CanvasDiscussionEntry(
                id=3,
                user_id=103,
                user_name="Carol Williams",
                discussion_id=12345,
                message=(
                    "<p>I agree with <strong>Bob</strong>. "
                    "I recommend practicing with simple examples first.</p>"
                ),
                created_at=datetime(2025, 10, 2, 15, 30, 0),
                parent_id=1,
                word_count=0,
            ),
            # Top-level post 2
            CanvasDiscussionEntry(
                id=4,
                user_id=104,
                user_name="David Brown",
                discussion_id=12345,
                message=(
                    "<p>For me, the hardest part is debugging. "
                    "<em>Error messages</em> can be confusing!</p>"
                ),
                created_at=datetime(2025, 10, 2, 11, 0, 0),
                parent_id=None,
                word_count=0,
            ),
            # Reply to post 2
            CanvasDiscussionEntry(
                id=5,
                user_id=101,
                user_name="Alice Johnson",
                discussion_id=12345,
                message=(
                    "<p>David, I recommend reading the error messages carefully. "
                    "They usually point you to the exact line that's causing issues.</p>"
                ),
                created_at=datetime(2025, 10, 2, 16, 0, 0),
                parent_id=4,
                word_count=0,
            ),
        ]
        return entries

    def test_exporter_creates_correct_directory_structure(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify the exporter creates the correct directory structure.

        EXPECTED STRUCTURE (without course_name):
        exports/
          class_{course_id}/
            {sanitized_discussion_title}/
              discussion.md

        WHY: The directory structure uses human-readable names for easier navigation.
        When no course_name is provided, it falls back to class_{course_id}.
        """
        # ARRANGE
        exporter = MarkdownExporter()
        course_id = 999

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=course_id,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
        )

        # ASSERT
        # Discussion title is "Week 1: Introduction to Python"
        # Sanitized it becomes "Week_1_Introduction_to_Python"
        expected_path = (
            Path(temp_export_dir)
            / f"class_{course_id}"
            / "Week_1_Introduction_to_Python"
            / "discussion.md"
        )
        assert Path(output_file) == expected_path
        assert Path(output_file).exists()

    def test_exporter_uses_course_name_when_provided(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify the exporter uses human-readable course name when provided.

        EXPECTED STRUCTURE (with course_name):
        exports/
          {sanitized_course_name}/
            {sanitized_discussion_title}/
              discussion.md

        WHY: Human-readable names make it much easier to find and identify
        exported discussions without needing to remember course IDs.
        """
        # ARRANGE
        exporter = MarkdownExporter()
        course_id = 999
        course_name = "Introduction to Computer Science"

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=course_id,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
            course_name=course_name,
        )

        # ASSERT
        expected_path = (
            Path(temp_export_dir)
            / "Introduction_to_Computer_Science"
            / "Week_1_Introduction_to_Python"
            / "discussion.md"
        )
        assert Path(output_file) == expected_path
        assert Path(output_file).exists()

        # Also verify the content includes the course name
        with open(output_file, encoding="utf-8") as f:
            content = f.read()
        assert "**Course:** Introduction to Computer Science" in content
        assert f"(ID: {course_id})" in content

    def test_exported_markdown_contains_discussion_metadata(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify the exported markdown includes all discussion metadata.

        EXPECTED CONTENT:
        - Discussion title as H1 header
        - Course ID and Discussion ID
        - Posted date
        - Total entries count
        - Discussion type
        - Graded status

        WHY: This metadata provides essential context for LLM processing and
        human review. Without it, the reader wouldn't know what discussion
        they're looking at.
        """
        # ARRANGE
        exporter = MarkdownExporter()
        course_id = 999

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=course_id,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
        )

        # Read the file
        with open(output_file, encoding="utf-8") as f:
            content = f.read()

        # ASSERT - Check for key metadata
        assert "# Week 1: Introduction to Python" in content  # Title as H1
        assert f"**Course ID:** {course_id}" in content
        assert f"**Discussion ID:** {sample_discussion.id}" in content
        assert "**Posted:** October 01, 2025" in content
        assert f"**Total Entries:** {len(sample_entries)}" in content
        assert "**Type:** threaded" in content
        assert "**Graded:** Yes" in content  # Has assignment_id

    def test_exported_markdown_includes_discussion_prompt(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify the discussion prompt is included with HTML cleaned.

        WHY: The prompt provides critical context for understanding what students
        were asked to discuss. We need to ensure HTML is properly cleaned for
        markdown readability.
        """
        # ARRANGE
        exporter = MarkdownExporter()

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=999,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
        )

        with open(output_file, encoding="utf-8") as f:
            content = f.read()

        # ASSERT
        assert "## Discussion Prompt" in content
        # HTML <p> tags should be removed
        assert "Discuss your experience with Python" in content
        assert "<p>" not in content

    def test_exported_markdown_preserves_threading_hierarchy(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify threaded conversation structure is preserved.

        EXPECTED STRUCTURE:
        - Top-level posts appear first (parent_id = None)
        - Replies appear indented under their parent
        - Chronological ordering within each level

        WHY: Threading is crucial for understanding conversation flow. LLMs and
        humans need to see who replied to whom.
        """
        # ARRANGE
        exporter = MarkdownExporter()

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=999,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
        )

        with open(output_file, encoding="utf-8") as f:
            content = f.read()

        # ASSERT
        # Check that all authors appear
        assert "Alice Johnson" in content
        assert "Bob Smith" in content
        assert "Carol Williams" in content
        assert "David Brown" in content

        # Check threading headers (### for top-level, #### for replies)
        assert "### Alice Johnson" in content  # Top-level post
        assert "#### Bob Smith" in content  # Reply (one level deeper)

    def test_html_cleaning_removes_tags_preserves_formatting(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify HTML tags are properly cleaned while preserving text formatting.

        HTML TO MARKDOWN CONVERSIONS:
        - <strong> → **bold**
        - <em> → *italic*
        - <p> → paragraph breaks
        - <br> → line breaks
        - Remove all other tags

        WHY: Canvas stores messages as HTML, but we want clean markdown for
        LLM consumption. Proper conversion ensures no information is lost.
        """
        # ARRANGE
        exporter = MarkdownExporter()

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=999,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
        )

        with open(output_file, encoding="utf-8") as f:
            content = f.read()

        # ASSERT
        # Strong tags should become bold markdown
        assert "**Bob**" in content  # From entry 3

        # Emphasis tags should become italic markdown
        assert "*Error messages*" in content  # From entry 4

        # Raw HTML tags should be gone
        assert "<strong>" not in content
        assert "<em>" not in content
        assert "<p>" not in content

    def test_export_with_analysis_includes_grading_summary(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify grading analysis is included when requested.

        WHY: When include_analysis=True, instructors want to see grading data
        in the markdown for context when generating feedback.
        """
        # ARRANGE
        exporter = MarkdownExporter()

        # Create sample analysis
        analysis = DiscussionAnalysis(
            discussion_id=12345,
            discussion_title="Week 1: Introduction to Python",
            course_id=999,
            analysis_date=datetime.now(),
            total_students=3,
            participating_students=3,
            participation_rate=100.0,
            total_posts=5,
            total_replies=3,
            total_words=150,
            average_words_per_post=30.0,
            average_score=85.0,
            students_meeting_requirements=2,
            rubric_used="Default",
            student_participation=[
                StudentParticipation(
                    user_id=101,
                    user_name="Alice Johnson",
                    total_posts=2,
                    reply_count=1,
                    total_word_count=50,
                    total_score=90.0,
                    meets_requirements=True,
                )
            ],
        )

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=999,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
            include_analysis=True,
            analysis=analysis,
        )

        with open(output_file, encoding="utf-8") as f:
            content = f.read()

        # ASSERT
        assert "## Participation Statistics" in content
        assert "**Total Students:** 3" in content
        assert "## Grading Summary" in content
        assert "Alice Johnson" in content
        assert "90.0" in content  # Score

    def test_export_without_analysis_excludes_grading_data(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify grading analysis is excluded when not requested.

        WHY: Sometimes we just want the raw discussion without grading context.
        This tests that include_analysis=False properly excludes that data.
        """
        # ARRANGE
        exporter = MarkdownExporter()

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=999,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
            include_analysis=False,
        )

        with open(output_file, encoding="utf-8") as f:
            content = f.read()

        # ASSERT - Analysis sections should NOT be present
        assert "## Participation Statistics" not in content
        assert "## Grading Summary" not in content

    def test_export_handles_empty_discussion(self, temp_export_dir, sample_discussion):
        """
        TEST: Verify exporter handles discussions with no entries gracefully.

        EDGE CASE: What happens when there are no student posts?

        WHY: This is a realistic scenario (newly created discussions, or
        discussions where all posts were deleted). The exporter should handle
        this without crashing.
        """
        # ARRANGE
        exporter = MarkdownExporter()
        empty_entries = []

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=999,
            discussion=sample_discussion,
            entries=empty_entries,
            output_dir=temp_export_dir,
        )

        with open(output_file, encoding="utf-8") as f:
            content = f.read()

        # ASSERT
        assert Path(output_file).exists()
        assert "**Total Entries:** 0" in content
        assert "## Discussion Thread" in content

    def test_word_count_included_in_posts(self, temp_export_dir, sample_discussion, sample_entries):
        """
        TEST: Verify each post includes its word count.

        WHY: Word count is a key metric for evaluating substantive participation.
        It should be clearly visible in the exported markdown.
        """
        # ARRANGE
        exporter = MarkdownExporter()

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=999,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
        )

        with open(output_file, encoding="utf-8") as f:
            content = f.read()

        # ASSERT
        # Each post should have "Words: X" metadata
        assert "Words:" in content
        # Should appear multiple times (once per post)
        assert content.count("Words:") == len(sample_entries)

    def test_timestamps_formatted_correctly(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify timestamps are formatted in human-readable format.

        EXPECTED FORMAT: "October 02, 2025 at 10:00 AM"

        WHY: Readable timestamps help understand the timeline of the discussion.
        This format is both human-friendly and provides sufficient detail.
        """
        # ARRANGE
        exporter = MarkdownExporter()

        # ACT
        output_file = exporter.export_discussion_to_markdown(
            course_id=999,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
        )

        with open(output_file, encoding="utf-8") as f:
            content = f.read()

        # ASSERT
        # Check for proper date formatting
        assert "Posted:" in content  # Timestamp metadata label
        # Should have month name, not number
        assert "October" in content

    def test_raises_error_when_analysis_missing_but_required(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """
        TEST: Verify ValueError is raised when include_analysis=True but analysis=None.

        WHY: This catches a common usage error. If someone requests analysis
        inclusion but doesn't provide the data, we should fail fast with a
        clear error message.
        """
        # ARRANGE
        exporter = MarkdownExporter()

        # ACT & ASSERT
        with pytest.raises(ValueError, match="analysis parameter required"):
            exporter.export_discussion_to_markdown(
                course_id=999,
                discussion=sample_discussion,
                entries=sample_entries,
                output_dir=temp_export_dir,
                include_analysis=True,  # Requesting analysis...
                analysis=None,  # ...but not providing it!
            )

    def test_grading_summary_shows_ai_columns_when_band_data_present(
        self, temp_export_dir, sample_discussion, sample_entries
    ):
        """Verify markdown grading summary expands for AI discussion profile fields."""
        exporter = MarkdownExporter()

        analysis = DiscussionAnalysis(
            discussion_id=12345,
            discussion_title="Week 1: Introduction to Python",
            course_id=999,
            analysis_date=datetime.now(),
            total_students=1,
            participating_students=1,
            participation_rate=100.0,
            total_posts=3,
            total_replies=2,
            total_words=640,
            average_words_per_post=213.3,
            average_score=95.0,
            students_meeting_requirements=1,
            rubric_used="AI Discussion Rubric",
            student_participation=[
                StudentParticipation(
                    user_id=101,
                    user_name="Alice Johnson",
                    total_posts=3,
                    reply_count=2,
                    total_word_count=640,
                    total_score=95.0,
                    meets_requirements=True,
                    band="excellent",
                    post_details=[
                        {"post_role": "research", "word_count": 140, "timing_status": "on_time"},
                        {
                            "post_role": "restatement",
                            "word_count": 320,
                            "timing_status": "late_post2",
                        },
                        {
                            "post_role": "reply",
                            "word_count": 180,
                            "timing_status": "on_time",
                        },
                    ],
                )
            ],
        )

        output_file = exporter.export_discussion_to_markdown(
            course_id=999,
            discussion=sample_discussion,
            entries=sample_entries,
            output_dir=temp_export_dir,
            include_analysis=True,
            analysis=analysis,
        )

        with open(output_file, encoding="utf-8") as file_handle:
            content = file_handle.read()

        assert "| Student | Posts | Words | Replies | P1 | P2 | P3 | Band | Timing" in content
        assert "| Alice Johnson | 3 | 640 | 2 | 140 | 320 | 180 | excellent" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
