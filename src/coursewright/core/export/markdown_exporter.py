#!/usr/bin/env python3
"""
Markdown Exporter
=================
Export discussion data to LLM-friendly markdown format.

This module creates markdown files optimized for Large Language Model (LLM) processing.
The format is designed to be:
- Human-readable for easy review
- Machine-parseable for LLM context
- Well-structured with clear threading/hierarchy
- Comprehensive with all relevant metadata

Date: October 23, 2025
"""

import re
from datetime import datetime
from pathlib import Path

from coursewright.core.canvas.models import (
    CanvasDiscussion,
    CanvasDiscussionEntry,
    DiscussionAnalysis,
)
from coursewright.core.export._destination import (
    default_export_base_directory,
    sanitize_name_for_filesystem,
)


class MarkdownExporter:
    """
    Exports discussion data to markdown format optimized for LLM processing.

    The markdown format includes:
    - Discussion metadata (title, prompt, dates)
    - Threaded conversation structure
    - Student names and timestamps
    - Word counts and participation metrics
    - Clean, hierarchical formatting

    This format is ideal for:
    - LLM context when generating feedback
    - Manual review and grading
    - Archiving discussion snapshots
    - Sharing discussion data
    """

    def __init__(self) -> None:
        """Initialize the markdown exporter"""
        self.date_format = "%B %d, %Y at %I:%M %p"

    def _sanitize_for_filesystem(self, name: str, max_length: int = 50) -> str:
        """Sanitize a name to be filesystem-safe (shared helper)."""
        return sanitize_name_for_filesystem(name, max_length)

    def export_discussion_to_markdown(
        self,
        course_id: int,
        discussion: CanvasDiscussion,
        entries: list[CanvasDiscussionEntry],
        output_dir: str | None = None,
        include_analysis: bool = False,
        analysis: DiscussionAnalysis | None = None,
        course_name: str | None = None,
    ) -> str:
        """
        Export a complete discussion to markdown format.

        Args:
            course_id: Canvas course ID
            discussion: CanvasDiscussion object with discussion metadata
            entries: List of CanvasDiscussionEntry objects (posts/replies)
            output_dir: Optional custom output directory. If None, uses default structure
            include_analysis: Whether to include grading analysis in the export
            analysis: Optional DiscussionAnalysis object (required if include_analysis=True)
            course_name: Optional human-readable course name (e.g., "Introduction to Python")

        Returns:
            Path to the created markdown file

        Raises:
            ValueError: If include_analysis=True but analysis is None
        """
        # Validate inputs
        if include_analysis and not analysis:
            raise ValueError("analysis parameter required when include_analysis=True")

        # Create output directory structure using human-readable names
        base_path = Path(output_dir) if output_dir else default_export_base_directory()

        # Use human-readable names with IDs as fallback
        if course_name:
            course_dir_name = f"{self._sanitize_for_filesystem(course_name)}"
        else:
            course_dir_name = f"class_{course_id}"

        discussion_name = self._sanitize_for_filesystem(discussion.title)
        discussion_dir_name = f"{discussion_name}"

        discussion_dir = base_path / course_dir_name / discussion_dir_name
        discussion_dir.mkdir(parents=True, exist_ok=True)

        # Create markdown filename
        output_file = discussion_dir / "discussion.md"

        # Generate markdown content
        markdown_content = self._generate_markdown_content(
            course_id=course_id,
            discussion=discussion,
            entries=entries,
            include_analysis=include_analysis,
            analysis=analysis,
            course_name=course_name,
        )

        # Write to file
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return str(output_file)

    def _generate_markdown_content(
        self,
        course_id: int,
        discussion: CanvasDiscussion,
        entries: list[CanvasDiscussionEntry],
        include_analysis: bool = False,
        analysis: DiscussionAnalysis | None = None,
        course_name: str | None = None,
    ) -> str:
        """
        Generate the complete markdown content for a discussion.

        This creates a well-structured markdown document with:
        1. Header with metadata
        2. Discussion prompt/instructions
        3. Threaded conversation
        4. Optional: Analysis summary

        Args:
            course_id: Canvas course ID
            discussion: Discussion topic metadata
            entries: All posts/replies in the discussion
            include_analysis: Whether to include grading analysis
            analysis: Optional analysis data
            course_name: Optional human-readable course name

        Returns:
            Complete markdown content as a string
        """
        sections = []

        # 1. Header with metadata
        sections.append(self._create_header(course_id, discussion, entries, course_name))

        # 2. Discussion prompt/instructions
        sections.append(self._create_prompt_section(discussion))

        # 3. Participation statistics (if analysis available)
        if include_analysis and analysis:
            sections.append(self._create_statistics_section(analysis))

        # 4. Threaded conversation
        sections.append(self._create_conversation_section(entries))

        # 5. Grading summary (if analysis available)
        if include_analysis and analysis:
            sections.append(self._create_grading_summary(analysis))

        # Join all sections with double newlines for readability
        return "\n\n".join(sections)

    def _create_header(
        self,
        course_id: int,
        discussion: CanvasDiscussion,
        entries: list[CanvasDiscussionEntry],
        course_name: str | None = None,
    ) -> str:
        """
        Create the markdown header with discussion metadata.

        This provides context about the discussion at a glance.
        """
        posted_date = "Unknown"
        if discussion.posted_at:
            posted_date = discussion.posted_at.strftime(self.date_format)

        # Build header with human-readable course name if available
        if course_name:
            course_line = f"**Course:** {course_name} (ID: {course_id})"
        else:
            course_line = f"**Course ID:** {course_id}"

        header = f"""# {discussion.title}

{course_line}
**Discussion ID:** {discussion.id}
**Posted:** {posted_date}
**Total Entries:** {len(entries)}
**Type:** {discussion.discussion_type or "Threaded"}
**Graded:** {"Yes" if discussion.assignment_id else "No"}"""

        if discussion.assignment_id:
            header += f"  \n**Assignment ID:** {discussion.assignment_id}"

        return header

    def _create_prompt_section(self, discussion: CanvasDiscussion) -> str:
        """
        Create the discussion prompt section.

        This shows what students were asked to discuss.
        """
        if not discussion.message:
            return "## Discussion Prompt\n\n*No prompt provided*"

        # Clean HTML from the message
        clean_message = self._clean_html(discussion.message)

        return f"""## Discussion Prompt

{clean_message}"""

    def _create_statistics_section(self, analysis: DiscussionAnalysis) -> str:
        """
        Create a statistics section showing participation overview.

        This helps understand the overall engagement at a glance.
        """
        meeting_requirements_ratio = (
            f"{analysis.students_meeting_requirements}/{analysis.total_students}"
        )
        return f"""## Participation Statistics

- **Total Students:** {analysis.total_students}
- **Participating Students:** {analysis.participating_students} ({analysis.participation_rate:.1f}%)
- **Total Posts:** {analysis.total_posts}
- **Total Replies:** {analysis.total_replies}
- **Total Words:** {analysis.total_words:,}
- **Average Words/Post:** {analysis.average_words_per_post:.1f}
- **Average Score:** {analysis.average_score:.1f}/100
- **Students Meeting Requirements:** {meeting_requirements_ratio}"""

    def _create_conversation_section(self, entries: list[CanvasDiscussionEntry]) -> str:
        """
        Create the main conversation section with threaded posts.

        This organizes all posts in a hierarchical structure that's easy
        for both humans and LLMs to understand.

        Structure:
        - Top-level posts (depth=0) are shown first
        - Replies are indented under their parent posts
        - Each post includes: author, timestamp, word count, content
        """
        section = "## Discussion Thread\n\n"

        if not entries:
            return section + "*No posts in this discussion*"

        # Organize entries by threading structure
        # First, create a lookup for parent-child relationships
        entries_by_id = {entry.id: entry for entry in entries}
        top_level_entries = [e for e in entries if e.parent_id is None or e.parent_id == 0]

        # Sort top-level entries by creation date (oldest first for chronological flow)
        top_level_entries.sort(key=lambda e: e.created_at if e.created_at else datetime.min)

        # Build threaded structure
        conversation_parts = []
        for top_entry in top_level_entries:
            conversation_parts.append(
                self._format_entry_thread(top_entry, entries, entries_by_id, depth=0)
            )

        section += "\n\n".join(conversation_parts)

        return section

    def _format_entry_thread(
        self,
        entry: CanvasDiscussionEntry,
        all_entries: list[CanvasDiscussionEntry],
        entries_by_id: dict[int, CanvasDiscussionEntry],
        depth: int = 0,
    ) -> str:
        """
        Format a single entry and its replies recursively.

        This creates a hierarchical structure with proper indentation.

        Args:
            entry: The entry to format
            all_entries: All entries in the discussion (for finding children)
            entries_by_id: Lookup dict for quick access
            depth: Current threading depth (0 = top level)

        Returns:
            Formatted markdown string for this entry and its children
        """
        # Format this entry
        indent = "  " * depth  # 2 spaces per depth level
        marker = "###" if depth == 0 else "####"  # Top-level posts get ### headings

        # Clean the display name
        author_name = entry.user_name or entry.user_display_name or f"User {entry.user_id}"

        # Format timestamp
        timestamp = "Unknown date"
        if entry.created_at:
            timestamp = entry.created_at.strftime(self.date_format)
        elif entry.posted_at:
            timestamp = entry.posted_at.strftime(self.date_format)

        # Clean HTML from message
        clean_message = self._clean_html(entry.message)

        # Build entry markdown
        entry_md = f"""{indent}{marker} {author_name}

{indent}*Posted: {timestamp} | Words: {entry.word_count}*

{indent}{clean_message}"""

        # Find and format replies to this entry
        replies = [e for e in all_entries if e.parent_id == entry.id]

        if replies:
            # Sort replies chronologically
            replies.sort(key=lambda e: e.created_at if e.created_at else datetime.min)

            reply_parts = [entry_md]
            for reply in replies:
                reply_md = self._format_entry_thread(reply, all_entries, entries_by_id, depth + 1)
                reply_parts.append(reply_md)

            return "\n\n".join(reply_parts)

        return entry_md

    def _create_grading_summary(self, analysis: DiscussionAnalysis) -> str:
        """
        Create a grading summary section.

        This shows individual student scores and requirements.
        Useful for LLM context when generating feedback.
        """
        section = "## Grading Summary\n\n"
        has_ai_profile_data = any(
            getattr(student, "band", None) for student in analysis.student_participation
        )

        # Sort students by score (highest first)
        sorted_students = sorted(
            analysis.student_participation, key=lambda s: s.total_score, reverse=True
        )

        # Create markdown table
        if has_ai_profile_data:
            section += (
                "| Student | Posts | Words | Replies | P1 | P2 | P3 | Band | "
                "Timing | Score | Status |\n"
            )
            section += (
                "|---------|-------|-------|---------|----|----|----|------|"
                "--------|-------|--------|\n"
            )
        else:
            section += "| Student | Posts | Words | Replies | Score | Status |\n"
            section += "|---------|-------|-------|---------|-------|--------|\n"

        for student in sorted_students:
            status = "✓ Met" if student.meets_requirements else "✗ Not Met"

            if has_ai_profile_data:
                role_word_counts = {"research": 0, "restatement": 0, "reply": 0}
                timing_flags = set()
                for post_detail in student.post_details:
                    role = post_detail.get("post_role")
                    if role in role_word_counts and role_word_counts[role] == 0:
                        role_word_counts[role] = post_detail.get("word_count", 0)

                    timing_status = post_detail.get("timing_status")
                    if timing_status in {"late_post2", "late_post3", "unknown"}:
                        timing_flags.add(timing_status)

                section += (
                    f"| {student.user_name} | {student.total_posts} | "
                    f"{student.total_word_count} | {student.reply_count} | "
                    f"{role_word_counts['research']} | {role_word_counts['restatement']} | "
                    f"{role_word_counts['reply']} | {student.band or ''} | "
                    f"{', '.join(sorted(timing_flags))} | {student.total_score:.1f} | "
                    f"{status} |\n"
                )
            else:
                section += (
                    f"| {student.user_name} | {student.total_posts} | "
                    f"{student.total_word_count} | {student.reply_count} | "
                    f"{student.total_score:.1f} | {status} |\n"
                )

        # Add students not meeting requirements
        failing_students = [s for s in sorted_students if not s.meets_requirements]
        if failing_students:
            section += f"\n### Students Not Meeting Requirements ({len(failing_students)})\n\n"
            for student in failing_students:
                issues = (
                    ", ".join(student.missing_requirements)
                    if student.missing_requirements
                    else "Various issues"
                )
                section += f"- **{student.user_name}**: {issues}\n"

        return section

    def _clean_html(self, html_text: str) -> str:
        """
        Clean HTML tags from text while preserving structure.

        This creates clean, readable markdown from HTML content.
        - Removes HTML tags
        - Preserves line breaks
        - Handles common formatting

        Args:
            html_text: HTML content to clean

        Returns:
            Clean text suitable for markdown
        """
        if not html_text:
            return "*No content*"

        # Convert common HTML elements to markdown equivalents
        text = html_text

        # Preserve paragraph breaks
        text = re.sub(r"</p>\s*<p>", "\n\n", text)
        text = re.sub(r"<br\s*/?>", "\n", text)

        # Convert lists
        text = re.sub(r"<li>", "- ", text)
        text = re.sub(r"</li>", "\n", text)

        # Convert strong/bold
        text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text)
        text = re.sub(r"<b>(.*?)</b>", r"**\1**", text)

        # Convert emphasis/italic
        text = re.sub(r"<em>(.*?)</em>", r"*\1*", text)
        text = re.sub(r"<i>(.*?)</i>", r"*\1*", text)

        # Remove all remaining HTML tags
        text = re.sub(r"<[^>]+>", "", text)

        # Clean up whitespace
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)  # Max 2 consecutive newlines
        text = text.strip()

        # Decode HTML entities
        text = self._decode_html_entities(text)

        return text

    def _decode_html_entities(self, text: str) -> str:
        """
        Decode common HTML entities to their character equivalents.

        Args:
            text: Text with HTML entities

        Returns:
            Text with decoded entities
        """
        import html

        return html.unescape(text)

    def extract_discussion_context(
        self, discussion: CanvasDiscussion, analysis: DiscussionAnalysis | None = None
    ) -> str:
        """
        Extract a concise discussion context for LLM prompts.

        This creates a brief summary of the discussion that can be included
        in LLM prompts without overwhelming the context window.

        Args:
            discussion: Discussion metadata
            analysis: Optional analysis data for statistics

        Returns:
            Brief discussion context string
        """
        # Clean the discussion prompt
        prompt = (
            self._clean_html(discussion.message) if discussion.message else "No prompt provided"
        )

        # Limit prompt length to avoid overwhelming context
        if len(prompt) > 500:
            prompt = prompt[:500] + "..."

        context = f"Discussion: {discussion.title}\n\n"
        context += f"Prompt: {prompt}\n\n"

        if analysis:
            context += "Class Statistics:\n"
            context += (
                f"- {analysis.participating_students}/{analysis.total_students} "
                "students participated\n"
            )
            context += f"- Average score: {analysis.average_score:.1f}/100\n"
            context += f"- Average words per post: {analysis.average_words_per_post:.1f}\n"

        return context.strip()
