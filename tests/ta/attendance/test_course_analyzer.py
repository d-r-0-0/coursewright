"""Tests for the course analyzer: markdown parsing, metrics, report."""

from __future__ import annotations

from pathlib import Path

from coursewright.ta.attendance.course_analyzer import (
    CourseReport,
    DiscussionMetrics,
    analyze_course_directory,
    parse_exported_discussion,
    render_summary_report,
)

# ---------------------------------------------------------------------------
# Sample markdown that mirrors the markdown exporter output
# ---------------------------------------------------------------------------

SAMPLE_DISCUSSION_MD = """\
# Week 1: Introduction Discussion

**Course:** Intro to CS (ID: 123)
**Discussion ID:** 456
**Posted:** January 10, 2026 at 09:00 AM
**Total Entries:** 5
**Type:** Threaded
**Graded:** Yes

## Discussion Prompt

Please introduce yourself.

## Discussion Thread

### Alice Johnson

*Posted: January 10, 2026 at 10:00 AM | Words: 150*

Hello everyone, I am Alice.

#### Bob Smith

*Posted: January 10, 2026 at 11:00 AM | Words: 80*

Welcome Alice!

##### Carol Davis

*Posted: January 10, 2026 at 12:00 PM | Words: 40*

Great to meet you both!

### Dave Lee

*Posted: January 11, 2026 at 09:00 AM | Words: 200*

Hi class, I'm Dave.

#### Eve Martin

*Posted: January 11, 2026 at 10:00 AM | Words: 60*

Nice to meet you Dave!
"""


# ---------------------------------------------------------------------------
# parse_exported_discussion
# ---------------------------------------------------------------------------


class TestParseExportedDiscussion:
    """Verify single-file markdown parsing."""

    def _write_and_parse(self, tmp_path: Path, content: str = SAMPLE_DISCUSSION_MD):
        md_file = tmp_path / "discussion.md"
        md_file.write_text(content, encoding="utf-8")
        return parse_exported_discussion(md_file)

    def test_title_extracted(self, tmp_path):
        m = self._write_and_parse(tmp_path)
        assert m.title == "Week 1: Introduction Discussion"

    def test_total_entries_from_metadata(self, tmp_path):
        m = self._write_and_parse(tmp_path)
        assert m.total_entries == 5

    def test_word_counts_parsed(self, tmp_path):
        m = self._write_and_parse(tmp_path)
        assert m.word_counts == [150, 80, 40, 200, 60]

    def test_total_words(self, tmp_path):
        m = self._write_and_parse(tmp_path)
        assert m.total_words == 530

    def test_average_words(self, tmp_path):
        m = self._write_and_parse(tmp_path)
        assert m.average_words_per_post == 106.0

    def test_top_level_posts(self, tmp_path):
        m = self._write_and_parse(tmp_path)
        assert m.top_level_posts == 2

    def test_reply_count(self, tmp_path):
        m = self._write_and_parse(tmp_path)
        assert m.reply_count == 3

    def test_max_reply_depth(self, tmp_path):
        m = self._write_and_parse(tmp_path)
        # ##### = depth 2 relative to ###
        assert m.max_reply_depth == 2

    def test_unique_authors(self, tmp_path):
        m = self._write_and_parse(tmp_path)
        assert m.unique_authors == 5

    def test_no_metadata_infers_count(self, tmp_path):
        """When **Total Entries:** is missing, infer from headings."""
        no_meta = SAMPLE_DISCUSSION_MD.replace("**Total Entries:** 5\n", "")
        m = self._write_and_parse(tmp_path, no_meta)
        assert m.total_entries == 5  # 2 top + 3 replies

    def test_empty_discussion(self, tmp_path):
        md_file = tmp_path / "discussion.md"
        md_file.write_text("# Empty\n\n**Total Entries:** 0\n")
        m = parse_exported_discussion(md_file)
        assert m.total_entries == 0
        assert m.word_counts == []
        assert m.average_words_per_post == 0.0


# ---------------------------------------------------------------------------
# analyze_course_directory
# ---------------------------------------------------------------------------


class TestAnalyzeCourseDirectory:
    """Verify directory-level scanning."""

    def _build_course(self, tmp_path: Path) -> Path:
        course_dir = tmp_path / "COURSE_A"
        for name in ("Disc_A", "Disc_B"):
            d = course_dir / name
            d.mkdir(parents=True)
            (d / "discussion.md").write_text(SAMPLE_DISCUSSION_MD)
        return course_dir

    def test_finds_all_discussions(self, tmp_path):
        course = self._build_course(tmp_path)
        report = analyze_course_directory(course)
        assert report.total_discussions == 2

    def test_total_posts(self, tmp_path):
        course = self._build_course(tmp_path)
        report = analyze_course_directory(course)
        assert report.total_posts == 10  # 5 each

    def test_total_words(self, tmp_path):
        course = self._build_course(tmp_path)
        report = analyze_course_directory(course)
        assert report.total_words == 1060

    def test_empty_directory(self, tmp_path):
        empty = tmp_path / "empty_course"
        empty.mkdir()
        report = analyze_course_directory(empty)
        assert report.total_discussions == 0


# ---------------------------------------------------------------------------
# render_summary_report
# ---------------------------------------------------------------------------


class TestRenderSummaryReport:
    """Verify markdown report generation."""

    def _make_report(self) -> CourseReport:
        d = DiscussionMetrics(
            title="Test Discussion",
            total_entries=5,
            top_level_posts=2,
            reply_count=3,
            word_counts=[100, 200, 150],
            max_reply_depth=1,
            author_names=["Alice", "Bob", "Alice"],
        )
        return CourseReport(
            course_directory="exports/COURSE_A",
            discussions=[d],
        )

    def test_contains_title(self):
        md = render_summary_report(self._make_report())
        assert "# Course Analysis Report" in md

    def test_contains_overall_table(self):
        md = render_summary_report(self._make_report())
        assert "Overall Engagement" in md

    def test_contains_per_discussion_row(self):
        md = render_summary_report(self._make_report())
        assert "Test Discussion" in md

    def test_contains_highlights(self):
        md = render_summary_report(self._make_report())
        assert "Most Active" in md

    def test_empty_report_has_no_highlights(self):
        md = render_summary_report(CourseReport())
        assert "Highlights" not in md
