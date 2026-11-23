"""Convert analyzer rubric points to Canvas assignment points."""

import math
from typing import Any


def discussion_points_settings(discussion_config: Any) -> dict[str, float | int]:
    """The configured rubric point values, in the analyzer's config keys.

    Merge into every analyzer config so a changed point scheme in the
    selected configuration actually changes the grade.
    """
    return {
        "substantial_post_points": discussion_config.substantial_post_points,
        "reply_points_each": discussion_config.reply_points_each,
        "max_replies_counted": discussion_config.max_replies_counted,
    }


def discussion_assignment_score(
    participation, points_possible: float, rubric_points: float = 100.0
) -> float:
    """Preserve the earned fraction of the analyzer's rubric maximum.

    Pass the analysis's ``rubric_points``: the maximum depends on the grading
    profile and the configured point values, so it is not always 100.  Call
    after feedback generation: an LLM role review can raise the analyzer's score.
    """
    score = float(participation.total_score)
    maximum = float(points_possible)
    if not all(math.isfinite(value) for value in (score, maximum, rubric_points)):
        raise ValueError("Discussion scores and point totals must be finite.")
    if rubric_points <= 0 or maximum < 0:
        raise ValueError("Rubric points must be positive; assignment points cannot be negative.")
    return min(max(score / rubric_points, 0.0), 1.0) * maximum
