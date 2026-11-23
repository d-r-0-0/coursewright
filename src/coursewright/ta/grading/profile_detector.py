"""Automatic grading-profile detection for a single discussion topic.

A semester can mix two very different discussion assignments:

* **AI discussions** — the three-post template documented in
  ``docs/AiDiscussions.md``: Post 1 (deep-research output), Post 2 (a
  300-word restatement replying to one's own Post 1), and Post 3 (a
  150-word substantive reply to a classmate).  Graded 30 / 40 / 30.
* **Regular ("legacy") discussions** — one substantial post plus a
  couple of peer replies.  Graded 60 + 20 + 20.

Grading a regular discussion with the AI rubric produces nonsense
feedback ("Post 2 (Restatement): 0.0/40 points (0 words)") for students
who did exactly what was asked, so we need to tell the two apart
*per discussion* rather than flipping one global switch.

Detection is deliberately conservative: we only claim a discussion is an
AI discussion when the instructions say so, or when the student posts
show the unmistakable self-reply threading the template requires.
Anything else falls back to the legacy profile.
"""

from __future__ import annotations

import re
from typing import Any

# Profile names shared with DiscussionAnalyzer / DiscussionConfig.
PROFILE_AI_DISCUSSION = "ai_discussion"
PROFILE_LEGACY = "legacy"
PROFILE_AUTO = "auto"

# How many distinct students must show the Post-1 → own-Post-2 threading
# before we trust structure alone to call a discussion an AI discussion.
# One student replying to themselves happens in any forum; two or more
# doing it is the assignment template showing through.
MINIMUM_SELF_REPLYING_STUDENTS: int = 2

# Phrases lifted from the AI discussion prompt itself.  Each entry is
# (compiled pattern, human-readable label used in the detection reason).
_TOPIC_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bpost\s*1\b.{0,40}\bresearch\b", re.IGNORECASE | re.DOTALL), "Post 1: research"),
    (
        re.compile(r"\bpost\s*2\b.{0,40}\brestate", re.IGNORECASE | re.DOTALL),
        "Post 2: restate it",
    ),
    (re.compile(r"\bdeep\s+research\b", re.IGNORECASE), "deep research"),
    (
        re.compile(r"\breply\s+to\s+your\s+own\s+post\b", re.IGNORECASE),
        "reply to your own post",
    ),
    (
        re.compile(
            r"\brestat(?:e|ing|ement)\b.{0,60}\byour\s+own\s+words\b", re.IGNORECASE | re.DOTALL
        ),
        "restate in your own words",
    ),
    (re.compile(r"\bai\s+discussion\b", re.IGNORECASE), "AI discussion"),
]

# Two independent markers keep an incidental mention of "deep research"
# in an otherwise ordinary prompt from hijacking the whole rubric.
MINIMUM_TOPIC_MARKERS: int = 2

# HTML tags are stripped before matching so markup between words (for
# example ``<strong>Post 1</strong>: research``) cannot hide a marker.
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _strip_html(raw_text: str | None) -> str:
    """Flatten HTML markup to plain text with normalised whitespace."""
    if not raw_text:
        return ""
    return " ".join(_HTML_TAG_PATTERN.sub(" ", raw_text).split())


def find_topic_markers(title: str | None, message: str | None) -> list[str]:
    """Return the AI-discussion phrases found in the topic instructions."""
    haystack = f"{_strip_html(title)} {_strip_html(message)}"
    return [label for pattern, label in _TOPIC_MARKERS if pattern.search(haystack)]


def count_self_replying_students(entries: list[Any] | None) -> int:
    """Count students who replied to one of their own posts in this topic.

    That self-reply is the structural fingerprint of Post 2
    (Restatement), which the AI template requires and ordinary
    discussions almost never produce.
    """
    if not entries:
        return 0

    author_by_entry_id: dict[int, int] = {}
    for entry in entries:
        entry_id = getattr(entry, "id", None)
        author_id = getattr(entry, "user_id", None)
        if entry_id is not None and author_id is not None:
            author_by_entry_id[entry_id] = author_id

    self_replying_student_ids: set[int] = set()
    for entry in entries:
        parent_id = getattr(entry, "parent_id", None)
        if parent_id in (None, 0):
            continue
        author_id = getattr(entry, "user_id", None)
        if author_id is not None and author_by_entry_id.get(parent_id) == author_id:
            self_replying_student_ids.add(author_id)

    return len(self_replying_student_ids)


def detect_grading_profile(
    title: str | None = None,
    message: str | None = None,
    entries: list[Any] | None = None,
) -> tuple[str, str]:
    """Decide whether a discussion should be graded AI-style or legacy-style.

    Args:
        title: The discussion topic title.
        message: The discussion topic body (the instructions students read).
        entries: Optional discussion entries, used as a structural tiebreak
            when the instructions are unavailable or unhelpful.

    Returns:
        A ``(profile, reason)`` tuple.  ``profile`` is either
        ``"ai_discussion"`` or ``"legacy"``; ``reason`` is a short
        human-readable explanation suitable for display in the TUI or a
        log line.
    """
    markers = find_topic_markers(title, message)
    if len(markers) >= MINIMUM_TOPIC_MARKERS:
        return PROFILE_AI_DISCUSSION, f"topic instructions mention {', '.join(markers)}"

    self_replying_students = count_self_replying_students(entries)
    if self_replying_students >= MINIMUM_SELF_REPLYING_STUDENTS:
        return (
            PROFILE_AI_DISCUSSION,
            f"{self_replying_students} students replied to their own post (Post 2 pattern)",
        )

    if markers:
        return (
            PROFILE_LEGACY,
            f"only one AI marker found ({markers[0]}) and no Post 2 threading",
        )
    return PROFILE_LEGACY, "no AI discussion markers in the topic instructions or post structure"
