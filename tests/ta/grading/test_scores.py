"""Synthetic checks for rubric-to-assignment score conversion."""

from types import SimpleNamespace

import pytest

from coursewright.ta.grading.scores import discussion_assignment_score


@pytest.mark.parametrize("points, expected", [(50, 40), (100, 80), (200, 160), (0, 0)])
def test_preserves_rubric_percentage(points, expected):
    student = SimpleNamespace(total_score=80, post_details=[])
    assert discussion_assignment_score(student, points) == expected


def test_explicit_rubric_maximum():
    student = SimpleNamespace(total_score=40, post_details=[])
    assert discussion_assignment_score(student, 200, rubric_points=50) == 160


@pytest.mark.parametrize("points", [None, float("nan"), -1])
def test_rejects_invalid_assignment_points(points):
    student = SimpleNamespace(total_score=80, post_details=[])
    with pytest.raises((TypeError, ValueError)):
        discussion_assignment_score(student, points)


def test_custom_point_scheme_scales_to_full_credit():
    """A perfect score on a 60-point scheme is full credit, not 60%."""
    from coursewright.ta.grading.analyzer import DiscussionAnalyzer

    analyzer = DiscussionAnalyzer(
        None,
        {
            "min_words": 300,
            "min_replies": 2,
            "rubric_file": None,
            "profile": "legacy",
            "substantial_post_points": 40.0,
            "reply_points_each": 10.0,
            "max_replies_counted": 2,
        },
    )
    assert analyzer.max_score() == 60
    perfect = SimpleNamespace(total_score=60, post_details=[])
    assert discussion_assignment_score(perfect, 50, analyzer.max_score()) == 50


def test_ai_profile_maximum_is_the_role_points():
    from coursewright.ta.grading.analyzer import DiscussionAnalyzer

    analyzer = DiscussionAnalyzer(
        None, {"rubric_file": None, "profile": "ai_discussion", "substantial_post_points": 40.0}
    )
    assert analyzer.max_score() == 100


def test_points_settings_come_from_the_selected_config():
    from coursewright.core.config import AppConfig
    from coursewright.ta.grading.scores import discussion_points_settings

    config = AppConfig()
    config.discussion.substantial_post_points = 45.0
    config.discussion.reply_points_each = 15.0
    config.discussion.max_replies_counted = 3
    assert discussion_points_settings(config.discussion) == {
        "substantial_post_points": 45.0,
        "reply_points_each": 15.0,
        "max_replies_counted": 3,
    }
