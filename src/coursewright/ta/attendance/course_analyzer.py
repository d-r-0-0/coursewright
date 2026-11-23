"""
Course Analyzer - Engagement Metrics from Exported Markdown
============================================================
Reads the markdown files produced by the discussion markdown exporter
and computes per-discussion and cross-course engagement statistics.

The heavy lifting is pure regex parsing - no Canvas API calls are
needed, so the analysis works entirely offline on exported data.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class DiscussionMetrics:
    """Engagement metrics parsed from a single exported discussion."""

    title: str = ""
    file_path: str = ""
    total_entries: int = 0
    top_level_posts: int = 0
    reply_count: int = 0
    word_counts: list[int] = field(default_factory=list)
    max_reply_depth: int = 0
    author_names: list[str] = field(default_factory=list)

    # Computed helpers -------------------------------------------------------

    @property
    def total_words(self) -> int:
        return sum(self.word_counts)

    @property
    def average_words_per_post(self) -> float:
        if not self.word_counts:
            return 0.0
        return statistics.mean(self.word_counts)

    @property
    def median_words_per_post(self) -> float:
        if not self.word_counts:
            return 0.0
        return statistics.median(self.word_counts)

    @property
    def unique_authors(self) -> int:
        return len(set(self.author_names))


@dataclass
class CourseReport:
    """Aggregated metrics across all discussions in a course."""

    course_directory: str = ""
    discussions: list[DiscussionMetrics] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))

    @property
    def total_discussions(self) -> int:
        return len(self.discussions)

    @property
    def total_posts(self) -> int:
        return sum(d.total_entries for d in self.discussions)

    @property
    def total_words(self) -> int:
        return sum(d.total_words for d in self.discussions)

    @property
    def all_word_counts(self) -> list[int]:
        counts: list[int] = []
        for d in self.discussions:
            counts.extend(d.word_counts)
        return counts

    @property
    def overall_average_words(self) -> float:
        counts = self.all_word_counts
        return statistics.mean(counts) if counts else 0.0

    @property
    def overall_median_words(self) -> float:
        counts = self.all_word_counts
        return statistics.median(counts) if counts else 0.0


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

# Regex patterns matching the markdown exporter output format.
# The exporter uses ``### Author`` for top-level posts (column 0) and
# ``  #### Author`` (2-space indent) for replies.  We anchor on
# optional leading whitespace so both forms are captured.
_TITLE_RE = re.compile(r"^# (.+)$", re.MULTILINE)
_TOTAL_ENTRIES_RE = re.compile(r"\*\*Total Entries:\*\*\s*(\d+)", re.MULTILINE)
_WORD_COUNT_RE = re.compile(r"\| Words:\s*(\d+)")
_THREAD_HEADER_RE = re.compile(r"^## Discussion Thread\s*$", re.MULTILINE)
_TOP_POST_RE = re.compile(r"^### (?!#).+", re.MULTILINE)
_REPLY_RE = re.compile(r"^\s+#{4,}\s+.+", re.MULTILINE)
_AUTHOR_RE = re.compile(r"^\s*#{3,}\s+(.+?)(?:\s*\(reply\))?\s*$", re.MULTILINE)


def parse_exported_discussion(markdown_path: Path) -> DiscussionMetrics:
    """
    Parse a single exported ``discussion.md`` into metrics.

    Args:
        markdown_path: Path to the markdown file.

    Returns:
        A populated :class:`DiscussionMetrics` instance.
    """
    text = markdown_path.read_text(encoding="utf-8")
    metrics = DiscussionMetrics(file_path=str(markdown_path))

    # Title
    title_match = _TITLE_RE.search(text)
    metrics.title = title_match.group(1).strip() if title_match else markdown_path.parent.name

    # Total entries from metadata header
    entries_match = _TOTAL_ENTRIES_RE.search(text)
    if entries_match:
        metrics.total_entries = int(entries_match.group(1))

    # Scope heading searches to the Discussion Thread section only,
    # so we don't accidentally count grading-summary headings or
    # other structural markup outside the actual posts.
    thread_match = _THREAD_HEADER_RE.search(text)
    thread_text = text[thread_match.end() :] if thread_match else text

    # Word counts from each post's metadata line (within thread)
    metrics.word_counts = [int(m.group(1)) for m in _WORD_COUNT_RE.finditer(thread_text)]

    # Top-level vs reply counts (within thread section)
    metrics.top_level_posts = len(_TOP_POST_RE.findall(thread_text))
    reply_matches = _REPLY_RE.findall(thread_text)
    metrics.reply_count = len(reply_matches)

    # Deepest reply depth (#### = depth 1, ##### = depth 2, ...)
    # The reply regex matches leading whitespace + hashes, so we
    # strip() before counting hash characters.
    max_depth = 0
    for match in _REPLY_RE.finditer(thread_text):
        stripped_heading = match.group(0).strip()
        heading_hashes = stripped_heading.split(" ")[0]
        # depth relative to top-level (###)
        depth = len(heading_hashes) - 3
        max_depth = max(max_depth, depth)
    metrics.max_reply_depth = max_depth

    # Author names (within thread section)
    metrics.author_names = [m.group(1).strip() for m in _AUTHOR_RE.finditer(thread_text)]

    # If total_entries wasn't in metadata, infer from post count
    if metrics.total_entries == 0:
        metrics.total_entries = metrics.top_level_posts + metrics.reply_count

    return metrics


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------


def analyze_course_directory(course_dir: Path) -> CourseReport:
    """
    Walk *course_dir* and parse every ``discussion.md`` found.

    Args:
        course_dir: Path to the exported course folder
            (e.g. ``exports/COURSE_CODE/``).

    Returns:
        A :class:`CourseReport` with one :class:`DiscussionMetrics`
        per discussion.
    """
    report = CourseReport(course_directory=str(course_dir))

    markdown_files = sorted(course_dir.rglob("discussion.md"))
    for md_file in markdown_files:
        metrics = parse_exported_discussion(md_file)
        report.discussions.append(metrics)

    return report


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_summary_report(report: CourseReport) -> str:
    """
    Render a :class:`CourseReport` as a markdown summary.

    The output is written to ``_ANALYSIS/summary_report.md`` inside
    the course export directory.

    Args:
        report: The aggregated course report.

    Returns:
        The complete markdown string.
    """
    lines: list[str] = []
    lines.append("# Course Analysis Report")
    lines.append("")
    lines.append(f"**Source:** `{report.course_directory}`")
    lines.append(f"**Generated:** {report.generated_at}")
    lines.append(f"**Discussions Analyzed:** {report.total_discussions}")
    lines.append("")

    # ------------------------------------------------------------------
    # Overall engagement summary
    # ------------------------------------------------------------------
    lines.append("## Overall Engagement")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Discussions | {report.total_discussions} |")
    lines.append(f"| Total Posts | {report.total_posts} |")
    lines.append(f"| Total Words | {report.total_words:,} |")
    lines.append(f"| Avg Words / Post | {report.overall_average_words:.1f} |")
    lines.append(f"| Median Words / Post | {report.overall_median_words:.1f} |")
    lines.append("")

    # ------------------------------------------------------------------
    # Per-discussion breakdown
    # ------------------------------------------------------------------
    lines.append("## Per-Discussion Breakdown")
    lines.append("")
    lines.append("| Discussion | Posts | Replies | Depth | Words | Avg W/P | Authors |")
    lines.append("|------------|-------|---------|-------|-------|---------|---------|")

    for d in report.discussions:
        title_short = d.title[:35] + "..." if len(d.title) > 35 else d.title
        lines.append(
            f"| {title_short} "
            f"| {d.total_entries} "
            f"| {d.reply_count} "
            f"| {d.max_reply_depth} "
            f"| {d.total_words:,} "
            f"| {d.average_words_per_post:.0f} "
            f"| {d.unique_authors} |"
        )

    lines.append("")

    # ------------------------------------------------------------------
    # Highlights
    # ------------------------------------------------------------------
    if report.discussions:
        most_posts = max(report.discussions, key=lambda d: d.total_entries)
        most_words = max(report.discussions, key=lambda d: d.total_words)
        deepest = max(report.discussions, key=lambda d: d.max_reply_depth)

        lines.append("## Highlights")
        lines.append("")
        lines.append(f"- **Most Active:** {most_posts.title} ({most_posts.total_entries} posts)")
        lines.append(
            f"- **Longest Discussion:** {most_words.title} ({most_words.total_words:,} words)"
        )
        lines.append(f"- **Deepest Thread:** {deepest.title} (depth {deepest.max_reply_depth})")
        lines.append("")

    return "\n".join(lines)
