"""Create graded discussion topics in Canvas modules (the safe-write half).

Ported from COURSE_BUILDER ``scripts/discussions/add_discussions.py``.
Every write goes through ``SafeCanvasWriter`` (AGENTS.md 3.11): a writer
built without ``execute`` only previews, and nothing here prints or asks
questions (3.8), so the command line decides how to show the result.

Deliberate differences from the script:

- The script's own whole-course backup is replaced by the safe writer's
  timestamped backup before each write.
- Choosing the course and the modules interactively is ``ui/``'s job;
  `select_unit_modules` is the pure selection rule it can call.
- A Canvas refusal for one module is recorded and the next module still
  runs (as in the script).  A failed backup aborts everything.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from coursewright.core.canvas.safe_write import (
    SafeCanvasWriter,
    WriteFailedError,
    WriteOutcome,
)
from coursewright.design.discussions.drafting import DiscussionDraft

# The script's standard pattern: odd-numbered units up to 13.
DEFAULT_LAST_UNIT = 13
DEFAULT_POINTS = 100
FIRST_MODULE_POSITION = 1

UNIT_NUMBER_PATTERN = re.compile(r"Unit\s+(\d+)")

# Generic prompts used when no draft is supplied for a module.
DEFAULT_PROMPT_INTRO = """<p><strong>Getting to Know You</strong></p>
<p>Welcome to the course! Let's start by getting to know each other.</p>
<ol>
  <li>Introduce yourself - What's your name and what are you studying?</li>
  <li>What interests you most about this subject?</li>
  <li>What do you hope to learn or achieve in this course?</li>
</ol>
<p>Then, reply to at least one classmate's introduction.</p>
"""

DEFAULT_PROMPT_GENERAL = """<p><strong>Reflect on this unit's content:</strong></p>
<ol>
  <li>What was the most important or interesting concept you learned?</li>
  <li>How does it relate to real-world scenarios or your experience?</li>
  <li>What questions do you still have?</li>
</ol>
<p>Then, reply to at least one classmate's post with thoughtful feedback or additional insights.</p>
"""


@dataclass
class DiscussionCreationReport:
    """What a creation run did (or, in a dry run, would do)."""

    executed: bool
    topic_outcomes: list[WriteOutcome] = field(default_factory=list)
    module_item_outcomes: list[WriteOutcome] = field(default_factory=list)
    modules_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def discussions_created(self) -> int:
        """Topics Canvas accepted (always 0 in a dry run)."""
        return len(self.topic_outcomes) if self.executed else 0

    @property
    def discussions_added(self) -> int:
        """Topics placed in a module (always 0 in a dry run)."""
        return len(self.module_item_outcomes) if self.executed else 0

    @property
    def discussions_planned(self) -> int:
        """Topics the run would create or did attempt."""
        return len(self.topic_outcomes)


def unit_number_of(module_name: str) -> str | None:
    """Return the digits after ``Unit`` in a module name, as written."""
    match = UNIT_NUMBER_PATTERN.search(module_name)
    return match.group(1) if match else None


def select_unit_modules(
    modules: Iterable[dict[str, Any]], *, last_unit: int = DEFAULT_LAST_UNIT
) -> list[str]:
    """Return names of the odd-numbered ``Unit N`` modules up to *last_unit*."""
    selected: list[str] = []
    for module in modules:
        unit_number = unit_number_of(module["name"])
        if unit_number is None:
            continue
        number = int(unit_number)
        if number % 2 == 1 and number <= last_unit:
            selected.append(module["name"])
    return selected


def is_first_unit(module_name: str) -> bool:
    """True when the module is Unit 1 (``Unit 01`` and ``Unit 1`` both count)."""
    unit_number = unit_number_of(module_name)
    return unit_number is not None and int(unit_number) == 1


def default_discussion_for_module(module_name: str) -> tuple[str, str]:
    """Return ``(title, message_html)`` the script would use for a module."""
    if is_first_unit(module_name):
        return "U01D01 - Getting to Know You", DEFAULT_PROMPT_INTRO
    unit_number = unit_number_of(module_name)
    if unit_number is not None:
        return f"U{unit_number}D01 - {module_name} Discussion", DEFAULT_PROMPT_GENERAL
    return f"{module_name} Discussion", DEFAULT_PROMPT_GENERAL


def build_discussion_topic_payload(
    title: str, message_html: str, *, points: int = DEFAULT_POINTS, published: bool = False
) -> dict[str, Any]:
    """Build the Canvas payload for a graded, threaded discussion topic."""
    return {
        "title": title,
        "message": message_html,
        "discussion_type": "threaded",
        "published": published,
        "require_initial_post": True,  # students must post before seeing replies
        "assignment": {
            "points_possible": points,
            "grading_type": "points",
            "submission_types": ["discussion_topic"],
        },
    }


def create_discussions(
    writer: SafeCanvasWriter,
    course_id: int,
    modules: Sequence[dict[str, Any]],
    module_names: Iterable[str],
    *,
    drafts_by_module_name: dict[str, DiscussionDraft] | None = None,
    points: int = DEFAULT_POINTS,
) -> DiscussionCreationReport:
    """Create a discussion topic in each named module, first in its list.

    Whether anything is written is decided by the *writer*: built without
    ``execute`` it only previews.  Topics stay unpublished (Phase 5
    publishes them).

    Args:
        writer: A `SafeCanvasWriter`.
        course_id: Canvas course id.
        modules: The course's modules (``CanvasClient.get_modules``).
        module_names: Names of the modules that get a discussion.
        drafts_by_module_name: Reviewed drafts; a module without one gets
            the generic default prompt.
        points: Points possible for each discussion.

    Raises:
        BackupFailedError: a backup failed; nothing further is written.
    """
    wanted_names = set(module_names)
    drafts = drafts_by_module_name or {}
    report = DiscussionCreationReport(executed=writer.execute)

    for module in modules:
        module_name = module["name"]
        if module_name not in wanted_names:
            report.modules_skipped += 1
            continue

        default_title, default_message = default_discussion_for_module(module_name)
        draft = drafts.get(module_name)
        title = draft.title if draft else default_title
        message_html = draft.message_html if draft else default_message
        payload = build_discussion_topic_payload(title, message_html, points=points)

        try:
            topic_outcome = writer.create_discussion_topic(course_id, payload)
        except WriteFailedError as err:
            report.errors.append(f"Failed to create discussion for {module_name}: {err}")
            continue
        report.topic_outcomes.append(topic_outcome)

        # A dry run has no topic id yet; the preview shows the item without one.
        topic_id = topic_outcome.result["id"] if topic_outcome.executed else None
        item_payload = {
            "type": "Discussion",
            "content_id": topic_id,
            "position": FIRST_MODULE_POSITION,
        }
        try:
            item_outcome = writer.create_module_item(course_id, module["id"], item_payload)
        except WriteFailedError as err:
            report.errors.append(f"Created but failed to add to module {module_name}: {err}")
            continue
        report.module_item_outcomes.append(item_outcome)

    return report
