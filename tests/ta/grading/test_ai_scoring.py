"""Unit tests for the reusable AI-discussion scoring helpers."""

from __future__ import annotations

from coursewright.ta.grading import ai_scoring


class TestRolesFromPostDetails:
    """Cover both strategies of ``roles_from_post_details``."""

    def test_prefers_explicit_role_labels(self):
        """Explicit ``post_role`` labels win over structural inference."""
        post_details = [
            {"post_number": 1, "post_role": "research", "word_count": 500},
            {"post_number": 2, "post_role": "restatement", "word_count": 320},
            {"post_number": 3, "post_role": "reply", "word_count": 160},
        ]

        counts = ai_scoring.roles_from_post_details(post_details)

        assert counts == {"research": 500, "restatement": 320, "reply": 160}

    def test_falls_back_to_structural_relationship(self):
        """Unlabelled posts are sussed from ``parent_relationship``."""
        post_details = [
            {
                "post_number": 1,
                "post_role": "extra",
                "parent_relationship": "top_level",
                "word_count": 1226,
            },
            {
                "post_number": 2,
                "post_role": "extra",
                "parent_relationship": "self_reply",
                "word_count": 394,
            },
            {
                "post_number": 3,
                "post_role": "extra",
                "parent_relationship": "classmate_reply",
                "word_count": 170,
            },
        ]

        counts = ai_scoring.roles_from_post_details(post_details)

        assert counts == {"research": 1226, "restatement": 394, "reply": 170}

    def test_earliest_post_wins_each_role_slot(self):
        """When two posts share a relationship, the earliest fills the slot."""
        post_details = [
            {
                "post_number": 2,
                "parent_relationship": "classmate_reply",
                "word_count": 200,
            },
            {
                "post_number": 1,
                "parent_relationship": "classmate_reply",
                "word_count": 999,
            },
        ]

        counts = ai_scoring.roles_from_post_details(post_details)

        assert counts["reply"] == 999

    def test_missing_roles_default_to_zero(self):
        """Roles with no matching post report zero words."""
        post_details = [
            {
                "post_number": 1,
                "parent_relationship": "top_level",
                "word_count": 800,
            },
        ]

        counts = ai_scoring.roles_from_post_details(post_details)

        assert counts == {"research": 800, "restatement": 0, "reply": 0}

    def test_empty_post_details(self):
        """No posts yields an all-zero breakdown."""
        assert ai_scoring.roles_from_post_details([]) == {
            "research": 0,
            "restatement": 0,
            "reply": 0,
        }
