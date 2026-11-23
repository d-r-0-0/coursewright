"""
Tests for Discussion Analyzer
==============================

PURPOSE:
--------
These tests verify the CORE BUSINESS LOGIC of Discussorator - the grading algorithm.
This is the most critical component to test because it directly affects student grades.

WHAT WE'RE TESTING:
-------------------
1. Word counting is accurate
2. Reply counting is correct
3. Grading algorithm produces expected scores
4. Partial credit is calculated correctly
5. Edge cases (no posts, partial posts, etc.)

WHY THIS IS CRITICAL:
---------------------
If the grading algorithm is wrong, students get wrong grades. These tests ensure
the grading is fair, consistent, and mathematically correct.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from coursewright.core.canvas.models import (
    CanvasDiscussionEntry,
    CanvasUser,
    StudentParticipation,
)
from coursewright.ta.grading.analyzer import DiscussionAnalyzer


class TestGradingAlgorithm:
    """
    Tests for the core grading algorithm.

    GRADING RULES (from config):
    - Substantial post: 60 points (scaled by word count / 300)
    - Each reply: 20 points (up to 2 replies = 40 points)
    - Total possible: 100 points
    """

    def test_perfect_student_gets_100_points(self, grading_config):
        """
        TEST: Student who exceeds all requirements gets 100 points.

        SCENARIO:
        - 350 word substantial post (exceeds 300 minimum)
        - 3 replies (exceeds 2 minimum)

        EXPECTED:
        - Substantial: 60 points (full credit)
        - Replies: 40 points (2 × 20, capped at 2)
        - Total: 100 points
        """
        # Arrange: Create mock Canvas client
        mock_client = Mock()

        # Create analyzer
        analyzer = DiscussionAnalyzer(mock_client, grading_config)

        # Create a perfect student
        student = StudentParticipation(
            user_id=1,
            user_name="Perfect Student",
            total_posts=4,
            reply_count=3,
            total_word_count=500,
            post_details=[
                {"word_count": 350, "is_largest": True, "is_reply": False},
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
            ],
        )

        # Act: Grade the student
        analyzer._grade_participation(student)

        # Assert: Should get 100 points
        assert student.total_score == 100.0, "Perfect student should get 100 points"
        assert student.meets_requirements
        assert len(student.missing_requirements) == 0

    def test_partial_word_count_gets_partial_credit(self, grading_config):
        """
        TEST: Student with 200/300 words gets scaled credit.

        SCENARIO:
        - 200 word post (66.7% of 300 requirement)
        - 2 replies (meets requirement)

        EXPECTED:
        - Substantial: 40 points (60 × 0.667 = 40)
        - Replies: 40 points (2 × 20 = 40)
        - Total: 80 points
        """
        mock_client = Mock()
        analyzer = DiscussionAnalyzer(mock_client, grading_config)

        student = StudentParticipation(
            user_id=2,
            user_name="Partial Student",
            total_posts=3,
            reply_count=2,
            total_word_count=300,
            post_details=[
                {"word_count": 200, "is_largest": True, "is_reply": False},
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
            ],
        )

        analyzer._grade_participation(student)

        # 200/300 = 0.667, 0.667 × 60 = 40
        expected_total = 80.0

        assert student.total_score == expected_total
        assert not student.meets_requirements  # Doesn't meet word requirement
        assert any("200 words" in req for req in student.missing_requirements)

    def test_minimal_participation_gets_low_score(self, grading_config):
        """
        TEST: Student who barely participates gets low score.

        SCENARIO:
        - 50 word post (16.7% of requirement)
        - 0 replies

        EXPECTED:
        - Substantial: 10 points (60 × 0.167 = 10)
        - Replies: 0 points
        - Total: 10 points
        """
        mock_client = Mock()
        analyzer = DiscussionAnalyzer(mock_client, grading_config)

        student = StudentParticipation(
            user_id=3,
            user_name="Minimal Student",
            total_posts=1,
            reply_count=0,
            total_word_count=50,
            post_details=[
                {"word_count": 50, "is_largest": True, "is_reply": False},
            ],
        )

        analyzer._grade_participation(student)

        # 50/300 = 0.167, 0.167 × 60 = 10
        assert student.total_score == 10.0
        assert not student.meets_requirements
        assert "Need 2 replies" in student.missing_requirements

    def test_no_posts_gets_zero_points(self, grading_config):
        """
        TEST: Student with no posts gets 0 points.

        WHY: Edge case - ensure we handle non-participation correctly.
        """
        mock_client = Mock()
        analyzer = DiscussionAnalyzer(mock_client, grading_config)

        student = StudentParticipation(
            user_id=4,
            user_name="No Participation",
            total_posts=0,
            reply_count=0,
            total_word_count=0,
            post_details=[],
        )

        analyzer._grade_participation(student)

        assert student.total_score == 0.0
        assert not student.meets_requirements
        assert "No posts found" in student.missing_requirements

    def test_exactly_meets_requirements(self, grading_config):
        """
        TEST: Student who exactly meets requirements gets full credit.

        SCENARIO:
        - Exactly 300 words
        - Exactly 2 replies

        EXPECTED:
        - Should get 100 points
        - Should meet requirements
        """
        mock_client = Mock()
        analyzer = DiscussionAnalyzer(mock_client, grading_config)

        student = StudentParticipation(
            user_id=5,
            user_name="Exact Student",
            total_posts=3,
            reply_count=2,
            total_word_count=400,
            post_details=[
                {"word_count": 300, "is_largest": True, "is_reply": False},
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
            ],
        )

        analyzer._grade_participation(student)

        assert student.total_score == 100.0
        assert student.meets_requirements


class TestReplyCountingLogic:
    """
    Tests specifically for reply counting logic.

    WHY: Reply counting can be tricky with nested threads.
    We need to ensure it's counted correctly.
    """

    def test_one_reply_gets_20_points(self, grading_config):
        """
        TEST: Single reply gets 20 points.
        """
        mock_client = Mock()
        analyzer = DiscussionAnalyzer(mock_client, grading_config)

        student = StudentParticipation(
            user_id=1,
            user_name="One Reply",
            total_posts=2,
            reply_count=1,  # Only 1 reply
            total_word_count=350,
            post_details=[
                {"word_count": 300, "is_largest": True, "is_reply": False},
                {"word_count": 50, "is_largest": False, "is_reply": True},
            ],
        )

        analyzer._grade_participation(student)

        # Substantial: 60, Replies: 20 (1 × 20)
        assert student.total_score == 80.0

    def test_three_replies_capped_at_two(self, grading_config):
        """
        TEST: More than 2 replies still caps at 40 points.

        WHY: We only count up to 2 replies (as per config).
        Extra replies don't earn extra credit.
        """
        mock_client = Mock()
        analyzer = DiscussionAnalyzer(mock_client, grading_config)

        student = StudentParticipation(
            user_id=1,
            user_name="Many Replies",
            total_posts=5,
            reply_count=4,  # 4 replies, but only 2 count
            total_word_count=500,
            post_details=[
                {"word_count": 300, "is_largest": True, "is_reply": False},
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
            ],
        )

        analyzer._grade_participation(student)

        # Substantial: 60, Replies: 40 (capped at 2 × 20)
        assert student.total_score == 100.0


class TestEdgeCases:
    """
    Tests for unusual scenarios and edge cases.
    """

    def test_student_with_only_replies_no_substantial_post(self, grading_config):
        """
        TEST: What if student only makes replies, no substantial post?

        SCENARIO:
        - 0 word "substantial" post (or no post marked as substantial)
        - 3 replies

        EXPECTED:
        - Substantial: 0 points
        - Replies: 40 points (capped at 2)
        - Total: 40 points
        """
        mock_client = Mock()
        analyzer = DiscussionAnalyzer(mock_client, grading_config)

        student = StudentParticipation(
            user_id=1,
            user_name="Only Replies",
            total_posts=3,
            reply_count=3,
            total_word_count=150,
            post_details=[
                {"word_count": 0, "is_largest": True, "is_reply": False},  # "Largest" but 0 words
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
            ],
        )

        analyzer._grade_participation(student)

        # No substantial post = 0, replies = 40
        assert student.total_score == 40.0

    def test_empty_post_details_handled_gracefully(self, grading_config):
        """
        TEST: Student with empty post_details list doesn't crash.

        WHY: Defensive programming - ensure we handle missing data.
        """
        mock_client = Mock()
        analyzer = DiscussionAnalyzer(mock_client, grading_config)

        student = StudentParticipation(
            user_id=1,
            user_name="Empty Details",
            total_posts=0,
            reply_count=0,
            total_word_count=0,
            post_details=None,  # Could be None
        )

        # Should not crash
        analyzer._grade_participation(student)

        assert student.total_score == 0.0


class TestConfigurableGrading:
    """
    Tests that grading respects configuration values.

    WHY: Grading should be configurable. Different instructors
    may want different point distributions.
    """

    def test_custom_substantial_post_points(self):
        """
        TEST: Can we use custom points for substantial posts?

        SCENARIO: Configure 80 points for substantial post instead of 60.
        """
        mock_client = Mock()

        # Custom config
        custom_config = {
            "min_words": 300,
            "min_replies": 2,
            "substantial_post_points": 80.0,  # Custom!
            "reply_points_each": 10.0,  # Custom!
            "max_replies_counted": 2,
        }

        analyzer = DiscussionAnalyzer(mock_client, custom_config)

        student = StudentParticipation(
            user_id=1,
            user_name="Custom Grading",
            total_posts=3,
            reply_count=2,
            total_word_count=400,
            post_details=[
                {"word_count": 300, "is_largest": True, "is_reply": False},
                {"word_count": 50, "is_largest": False, "is_reply": True},
                {"word_count": 50, "is_largest": False, "is_reply": True},
            ],
        )

        analyzer._grade_participation(student)

        # Substantial: 80, Replies: 20 (2 × 10)
        expected_total = 100.0
        assert student.total_score == expected_total


class TestAiDiscussionProfile:
    """Tests for profile-based AI discussion scoring and role detection."""

    def _make_entry(
        self,
        entry_id: int,
        user_id: int,
        when: datetime,
        words: int,
        parent_id: int | None = None,
    ) -> CanvasDiscussionEntry:
        message = " ".join(["word"] * words)
        return CanvasDiscussionEntry(
            id=entry_id,
            user_id=user_id,
            created_at=when,
            parent_id=parent_id,
            discussion_id=999,
            message=message,
            word_count=words,
            depth=0 if parent_id in (None, 0) else 1,
        )

    def test_excellent_band_all_requirements_met(self):
        mock_client = Mock()
        analyzer = DiscussionAnalyzer(mock_client, {"profile": "ai_discussion"})

        now = datetime(2026, 5, 1, 10, 0, 0)
        other_top = self._make_entry(1, 2, now - timedelta(hours=1), 120)
        research = self._make_entry(2, 1, now, 320, None)
        restate = self._make_entry(3, 1, now + timedelta(hours=1), 310, 2)
        class_reply = self._make_entry(4, 1, now + timedelta(hours=2), 180, 1)

        post_lookup = {p.id: p for p in [other_top, research, restate, class_reply]}
        user = CanvasUser(id=1, name="Student One")
        timing_context = {
            "first_weekend_deadline": now + timedelta(days=2),
            "unit_due_deadline": now + timedelta(days=7),
        }

        participation = analyzer._analyze_individual_participation(
            user,
            [research, restate, class_reply],
            post_lookup=post_lookup,
            timing_context=timing_context,
        )

        assert participation.band == "excellent"
        assert participation.total_score == 100.0
        assert participation.meets_requirements

    def test_good_band_when_post3_between_100_and_149(self):
        analyzer = DiscussionAnalyzer(Mock(), {"profile": "ai_discussion"})

        student = StudentParticipation(
            user_id=1,
            user_name="Band Good",
            total_posts=3,
            post_details=[
                {"post_role": "research", "word_count": 220, "timing_status": "on_time"},
                {"post_role": "restatement", "word_count": 330, "timing_status": "on_time"},
                {"post_role": "reply", "word_count": 120, "timing_status": "on_time"},
            ],
        )

        analyzer._grade_participation_ai(student)

        assert student.band == "good"
        assert student.total_score == 94.0
        assert student.meets_requirements

    def test_satisfactory_band_when_post2_below_250(self):
        analyzer = DiscussionAnalyzer(Mock(), {"profile": "ai_discussion"})

        student = StudentParticipation(
            user_id=1,
            user_name="Band Sat",
            total_posts=3,
            post_details=[
                {"post_role": "research", "word_count": 220, "timing_status": "on_time"},
                {"post_role": "restatement", "word_count": 240, "timing_status": "on_time"},
                {"post_role": "reply", "word_count": 175, "timing_status": "on_time"},
            ],
        )

        analyzer._grade_participation_ai(student)

        assert student.band == "satisfactory"
        assert student.total_score == 92.0
        assert not student.meets_requirements

    def test_unsatisfactory_band_missing_two_roles(self):
        analyzer = DiscussionAnalyzer(Mock(), {"profile": "ai_discussion"})

        student = StudentParticipation(
            user_id=1,
            user_name="Band Unsat",
            total_posts=1,
            post_details=[
                {"post_role": "research", "word_count": 220, "timing_status": "on_time"},
            ],
        )

        analyzer._grade_participation_ai(student)

        assert student.band == "unsatisfactory"
        assert student.total_score == 30.0
        assert not student.meets_requirements

    def test_classification_orders_roles_chronologically(self):
        analyzer = DiscussionAnalyzer(Mock(), {"profile": "ai_discussion"})

        now = datetime(2026, 5, 3, 9, 0, 0)
        other_top = self._make_entry(10, 2, now - timedelta(hours=1), 80)
        first_post = self._make_entry(11, 1, now, 200)
        second_post = self._make_entry(12, 1, now + timedelta(hours=1), 310, 11)
        third_post = self._make_entry(13, 1, now + timedelta(hours=2), 151, 10)

        roles, warnings = analyzer._classify_ai_roles(
            1,
            [first_post, second_post, third_post],
            {p.id: p for p in [other_top, first_post, second_post, third_post]},
        )

        # Earliest post becomes research, next becomes restatement, next reply.
        assert roles["research"] == first_post.id
        assert roles["restatement"] == second_post.id
        assert roles["reply"] == third_post.id
        assert warnings == []

    def test_missing_self_reply_is_not_ambiguous(self):
        """Regression: a student who writes a substantial top-level post and
        a classmate-reply (but skips Post 2 / the self-reply) must NOT have
        their top-level post reshuffled into the restatement slot by the
        score-maximizing optimizer. Missing role ≠ ambiguous.
        """
        analyzer = DiscussionAnalyzer(Mock(), {"profile": "ai_discussion"})

        now = datetime(2026, 5, 23, 8, 0, 0)
        # Classmate's top-level post (someone else's research).
        other_top = self._make_entry(100, 2, now - timedelta(hours=1), 200)
        # Siddhanth's substantial Post 1 (1239 words → research).
        research = self._make_entry(101, 1, now, 1239, None)
        # Siddhanth's reply to the classmate (124 words → reply).
        class_reply = self._make_entry(102, 1, now + timedelta(hours=2), 124, 100)

        post_lookup = {p.id: p for p in [other_top, research, class_reply]}
        roles, warnings = analyzer._classify_ai_roles(1, [research, class_reply], post_lookup)

        assert roles["research"] == research.id
        assert roles["restatement"] is None
        assert roles["reply"] == class_reply.id
        assert warnings == []

    def test_timing_flags_recorded_without_penalty(self):
        analyzer = DiscussionAnalyzer(Mock(), {"profile": "ai_discussion"})

        student = StudentParticipation(
            user_id=1,
            user_name="Timing Student",
            total_posts=3,
            post_details=[
                {"post_role": "research", "word_count": 210, "timing_status": "on_time"},
                {"post_role": "restatement", "word_count": 320, "timing_status": "late_post2"},
                {"post_role": "reply", "word_count": 180, "timing_status": "late_post3"},
            ],
        )

        analyzer._grade_participation_ai(student)

        assert student.band == "excellent"
        assert student.total_score == 100.0
        assert "Timing flag: late_post2" in student.band_reasons
        assert "Timing flag: late_post3" in student.band_reasons


class TestAutomaticProfileDetection:
    """The 'auto' profile picks a rubric per discussion, not per semester.

    WHY THIS MATTERS: a semester that mixes AI discussions with regular
    ones cannot be graded from one global switch.  Grading a regular
    discussion with the AI rubric tells students they missed posts they
    were never assigned.
    """

    AI_TOPIC_MESSAGE = (
        "<h2>Post 1: research</h2><p>Use a deep research tool.</p>"
        "<h2>Post 2: restate it (reply to your own Post 1)</h2>"
        "<p>Write 300 words in your own words.</p>"
    )
    REGULAR_TOPIC_MESSAGE = "<p>Write a 300 word post and reply to two classmates.</p>"

    def _build_client(self, topic_message: str, entries: list) -> Mock:
        """Mock a Canvas client serving one topic with one student."""
        client = Mock()
        client.get_discussion_topic.return_value = SimpleNamespace(
            id=999, title="U07D01: Unit 07 Discussion", message=topic_message, posted_at=None
        )
        client.get_assignment_by_discussion.return_value = None
        client.get_course_users.return_value = [CanvasUser(id=1, name="Student One")]
        client.get_discussion_entries.return_value = entries
        return client

    def _make_entry(
        self, entry_id: int, user_id: int, words: int, parent_id: int | None = None
    ) -> CanvasDiscussionEntry:
        return CanvasDiscussionEntry(
            id=entry_id,
            user_id=user_id,
            created_at=datetime(2026, 5, 1, 10, 0, 0) + timedelta(hours=entry_id),
            parent_id=parent_id,
            discussion_id=999,
            message=" ".join(["word"] * words),
            word_count=words,
            depth=0 if parent_id in (None, 0) else 1,
        )

    def test_ai_topic_is_graded_with_the_ai_rubric(self):
        entries = [
            self._make_entry(1, user_id=1, words=320),
            self._make_entry(2, user_id=1, words=310, parent_id=1),
        ]
        analyzer = DiscussionAnalyzer(
            self._build_client(self.AI_TOPIC_MESSAGE, entries), {"profile": "auto"}
        )

        analysis = analyzer.analyze_discussion(course_id=1, discussion_id=999)

        assert analysis.grading_profile == "ai_discussion"
        assert "Post 1: research" in analysis.profile_detection_reason
        roles = {detail["post_role"] for detail in analysis.student_participation[0].post_details}
        assert "research" in roles and "restatement" in roles

    def test_regular_topic_is_graded_with_the_legacy_rubric(self):
        entries = [
            self._make_entry(1, user_id=1, words=320),
            self._make_entry(2, user_id=2, words=100),
            self._make_entry(3, user_id=1, words=100, parent_id=2),
        ]
        analyzer = DiscussionAnalyzer(
            self._build_client(self.REGULAR_TOPIC_MESSAGE, entries), {"profile": "auto"}
        )

        analysis = analyzer.analyze_discussion(course_id=1, discussion_id=999)

        assert analysis.grading_profile == "legacy"
        roles = {detail["post_role"] for detail in analysis.student_participation[0].post_details}
        assert roles == {"extra"}

    def test_explicit_profile_overrides_detection(self):
        """An explicit choice must never be second-guessed by detection."""
        entries = [self._make_entry(1, user_id=1, words=320)]
        analyzer = DiscussionAnalyzer(
            self._build_client(self.AI_TOPIC_MESSAGE, entries), {"profile": "legacy"}
        )

        analysis = analyzer.analyze_discussion(course_id=1, discussion_id=999)

        assert analysis.grading_profile == "legacy"
        assert analysis.profile_detection_reason is None
