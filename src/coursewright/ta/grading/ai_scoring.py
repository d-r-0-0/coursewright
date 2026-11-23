"""Reusable AI-discussion scoring helpers.

Centralises the point distribution and band logic used by the
``ai_discussion`` grading profile so it can be invoked both during
initial analysis and again later if an LLM "second set of eyes" pass
suggests a better role assignment.
"""

from __future__ import annotations

from typing import Any, TypedDict

RESEARCH_POINTS: float = 30.0
RESTATEMENT_POINTS: float = 40.0
REPLY_POINTS: float = 30.0
RESTATEMENT_MIN_WORDS: int = 300
REPLY_MIN_WORDS: int = 150

# Structural parent-relationship → AI-discussion role.  Mirrors the
# analyzer's structural classification so callers that only have the
# lightweight ``post_details`` dicts (the gradebook comment builders)
# can suss out roles the same way the full analyzer does:
#   * a top-level post is Post 1 (Research)
#   * a reply to one's own post is Post 2 (Restatement)
#   * a reply to a classmate is Post 3 (Reply)
_PARENT_RELATIONSHIP_TO_ROLE: dict[str, str] = {
    "top_level": "research",
    "self_reply": "restatement",
    "classmate_reply": "reply",
}

# The three AI-discussion roles, in rubric order.
_AI_ROLES: tuple[str, str, str] = ("research", "restatement", "reply")


class AiScoreBreakdown(TypedDict):
    """Per-role and total score breakdown for an AI discussion grade."""

    research_score: float
    restatement_score: float
    reply_score: float
    total_score: float


def has_explicit_ai_roles(post_details: list[dict[str, Any]] | None) -> bool:
    """Report whether these posts were graded under the AI rubric.

    Only the ``ai_discussion`` grading profile stamps posts with real
    ``research`` / ``restatement`` / ``reply`` roles; the legacy profile
    tags every post ``"extra"``.  Callers use this to decide whether an
    AI-shaped (30/40/30) breakdown is honest for this student, because
    inferring those roles from structure on a regular discussion invents
    requirements the student was never given.
    """
    return any(post_detail.get("post_role") in _AI_ROLES for post_detail in (post_details or []))


def is_ai_discussion_rubric(
    post_details: list[dict[str, Any]] | None,
    rubric_info: dict[str, Any] | None = None,
) -> bool:
    """Return True when a student should receive the AI Discussion (30/40/30) rubric.

    Explicit ``research`` / ``restatement`` / ``reply`` post roles always win.
    When a student has no posts (``post_details`` is empty), fall back to
    ``rubric_info["grading_profile"] == "ai_discussion"`` so zero-post
    students on an AI discussion still receive the 30/40/30 prompt and score
    breakdown rather than the legacy 60/40 format.
    """
    if has_explicit_ai_roles(post_details):
        return True
    profile = (rubric_info or {}).get("grading_profile")
    return isinstance(profile, str) and profile.strip().lower() == "ai_discussion"


def roles_from_post_details(post_details: list[dict[str, Any]]) -> dict[str, int]:
    """Suss out per-role word counts (research / restatement / reply).

    Two strategies, in order of trust:

    1. **Explicit labels** — when posts already carry a real
       ``post_role`` (the ``ai_discussion`` grading profile assigns
       ``"research"`` / ``"restatement"`` / ``"reply"``), use them
       directly.
    2. **Structural fallback** — when posts are unlabelled (the legacy
       profile tags every post ``"extra"``), infer the role from each
       post's ``parent_relationship`` using the same top-level /
       self-reply / classmate-reply mapping the analyzer uses.

    Returns a dict with ``research``, ``restatement``, and ``reply``
    word counts, defaulting to ``0`` for any role with no matching post.
    The earliest post (by ``post_number``) wins each role slot.
    """
    word_counts: dict[str, int] = {role: 0 for role in _AI_ROLES}

    ordered = sorted(post_details, key=lambda detail: detail.get("post_number", 0))

    # Strategy 1: trust explicit AI-role labels when present.
    found_explicit_role = False
    for post_detail in ordered:
        role = post_detail.get("post_role")
        if role in word_counts and word_counts[role] == 0:
            word_counts[role] = post_detail.get("word_count", 0)
            found_explicit_role = True
    if found_explicit_role:
        return word_counts

    # Strategy 2: infer roles from the post's structural relationship.
    for post_detail in ordered:
        role = _PARENT_RELATIONSHIP_TO_ROLE.get(post_detail.get("parent_relationship"))
        if role and word_counts[role] == 0:
            word_counts[role] = post_detail.get("word_count", 0)
    return word_counts


def score_role(word_count: int, required_words: int, max_points: float) -> float:
    """Award proportional credit, capped at the role's maximum points.

    A post with zero words (or where the role has no word requirement)
    earns nothing for that role.
    """
    if word_count <= 0 or required_words <= 0:
        return 0.0
    return min((word_count / required_words) * max_points, max_points)


def compute_ai_scores(
    research_words: int,
    restatement_words: int,
    reply_words: int,
    research_min_words: int,
) -> AiScoreBreakdown:
    """Compute the role-by-role score breakdown for an AI discussion grade.

    The research requirement is configurable per discussion (it lives on
    ``DiscussionRubric.min_word_count``); the restatement and reply
    requirements are fixed by the assignment template (300 and 150 words
    respectively).
    """
    research_score = score_role(research_words, research_min_words, RESEARCH_POINTS)
    restatement_score = score_role(restatement_words, RESTATEMENT_MIN_WORDS, RESTATEMENT_POINTS)
    reply_score = score_role(reply_words, REPLY_MIN_WORDS, REPLY_POINTS)
    total = round(research_score + restatement_score + reply_score, 1)
    return AiScoreBreakdown(
        research_score=research_score,
        restatement_score=restatement_score,
        reply_score=reply_score,
        total_score=total,
    )


def determine_band(
    *,
    roles_present_count: int,
    restatement_words: int,
    reply_words: int,
) -> str:
    """Translate per-role word counts into an AI discussion band label.

    Mirrors the existing analyzer logic so callers that regrade after an
    LLM mapping change land on the same band as a fresh analysis would.
    """
    if roles_present_count >= 2 and (
        roles_present_count < 3 or restatement_words < 250 or reply_words < 100
    ):
        return "satisfactory"
    if roles_present_count == 3 and (
        (250 <= restatement_words < 300) or (100 <= reply_words < 150)
    ):
        return "good"
    if (
        roles_present_count == 3
        and restatement_words >= RESTATEMENT_MIN_WORDS
        and reply_words >= REPLY_MIN_WORDS
    ):
        return "excellent"
    return "unsatisfactory"
