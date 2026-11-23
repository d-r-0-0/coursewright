"""Tests for automatic AI-vs-regular discussion detection.

WHY THIS MATTERS: a semester mixes AI discussions (Post 1 research /
Post 2 restatement / Post 3 reply, graded 30/40/30) with regular
discussions (one substantial post plus replies, graded 60/20/20).
Guessing wrong means a student who did everything right is told they
missed a "Post 2 (Restatement)" that was never assigned.
"""

from types import SimpleNamespace

from coursewright.ta.grading import profile_detector


def _entry(entry_id: int, user_id: int, parent_id: int | None = None) -> SimpleNamespace:
    """Build the minimal entry shape the detector reads."""
    return SimpleNamespace(id=entry_id, user_id=user_id, parent_id=parent_id)


AI_TOPIC_MESSAGE = """
<h2>Post 1: research</h2>
<p>Using a deep research tool, do a real investigation of the outcome.</p>
<h2>Post 2: restate it (reply to your own Post 1)</h2>
<p>Write approximately 300 words restating it in your own words.</p>
"""

REGULAR_TOPIC_MESSAGE = """
<p>Read the chapter and write a substantial post of at least 300 words
about one of the unit outcomes, then reply to two classmates.</p>
"""


class TestTopicInstructionDetection:
    """The topic's own instructions are the most trustworthy signal."""

    def test_ai_discussion_prompt_is_detected(self):
        profile, reason = profile_detector.detect_grading_profile(
            title="U07D01: Unit 07 Discussion",
            message=AI_TOPIC_MESSAGE,
        )

        assert profile == profile_detector.PROFILE_AI_DISCUSSION
        assert "Post 1: research" in reason

    def test_regular_prompt_falls_back_to_legacy(self):
        profile, reason = profile_detector.detect_grading_profile(
            title="U07D01: Unit 07 Discussion",
            message=REGULAR_TOPIC_MESSAGE,
        )

        assert profile == profile_detector.PROFILE_LEGACY
        assert "no AI discussion markers" in reason

    def test_html_markup_between_words_does_not_hide_markers(self):
        profile, _ = profile_detector.detect_grading_profile(
            title="Unit 09 Discussion",
            message=(
                "<p><strong>Post 1</strong>: <em>research</em></p>"
                "<p><strong>Post 2</strong>: restate it in your own words</p>"
            ),
        )

        assert profile == profile_detector.PROFILE_AI_DISCUSSION

    def test_single_incidental_marker_is_not_enough(self):
        """One passing mention of deep research must not flip the rubric."""
        profile, reason = profile_detector.detect_grading_profile(
            title="Unit 03 Discussion",
            message="<p>Feel free to use deep research tools if you like.</p>",
        )

        assert profile == profile_detector.PROFILE_LEGACY
        assert "only one AI marker" in reason


class TestPostStructureDetection:
    """When the instructions are unhelpful, the threading gives it away."""

    def test_multiple_self_replies_signal_an_ai_discussion(self):
        entries = [
            _entry(1, user_id=100),
            _entry(2, user_id=100, parent_id=1),  # Post 2 restatement
            _entry(3, user_id=200),
            _entry(4, user_id=200, parent_id=3),  # Post 2 restatement
        ]

        profile, reason = profile_detector.detect_grading_profile(
            title="Unit 05 Discussion", message="", entries=entries
        )

        assert profile == profile_detector.PROFILE_AI_DISCUSSION
        assert "2 students replied to their own post" in reason

    def test_one_self_reply_is_ordinary_forum_behaviour(self):
        entries = [
            _entry(1, user_id=100),
            _entry(2, user_id=100, parent_id=1),
            _entry(3, user_id=200, parent_id=1),  # ordinary peer reply
        ]

        profile, _ = profile_detector.detect_grading_profile(
            title="Unit 05 Discussion", message="", entries=entries
        )

        assert profile == profile_detector.PROFILE_LEGACY

    def test_peer_replies_only_stay_legacy(self):
        entries = [
            _entry(1, user_id=100),
            _entry(2, user_id=200, parent_id=1),
            _entry(3, user_id=300, parent_id=1),
        ]

        profile, _ = profile_detector.detect_grading_profile(
            title="Unit 05 Discussion", message="", entries=entries
        )

        assert profile == profile_detector.PROFILE_LEGACY

    def test_no_entries_and_no_message_is_legacy(self):
        profile, _ = profile_detector.detect_grading_profile()

        assert profile == profile_detector.PROFILE_LEGACY
