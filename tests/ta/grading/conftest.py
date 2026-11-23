"""Fixtures shared by the grading tests."""

import pytest


@pytest.fixture
def grading_config() -> dict:
    """
    Creates a standard grading configuration for testing.

    WHY: The analyzer needs configuration to grade discussions.
    This provides standard settings that tests can use.
    """
    return {
        "min_words": 300,
        "min_replies": 2,
        "rubric_file": None,  # Use default rubric
        "substantial_post_points": 60.0,
        "reply_points_each": 20.0,
        "max_replies_counted": 2,
    }
