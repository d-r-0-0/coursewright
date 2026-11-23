"""Restored discussorator feedback regressions with synthetic data."""

from unittest.mock import Mock

from coursewright.core.canvas.models import StudentParticipation
from coursewright.core.llm.client import LLMClient
from coursewright.ta.grading.feedback_generator import FeedbackGenerator


class TestSystemPrompts:
    """Test system prompt generation"""

    def test_system_prompt_content(self):
        """Test that system prompt has appropriate content"""
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        system_prompt = generator._create_system_prompt()

        assert "supportive" in system_prompt.lower()
        assert "encouraging" in system_prompt.lower()
        assert "specific" in system_prompt.lower()

    def test_student_prompt_includes_context(self):
        """Test that student prompt includes all necessary context"""
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        student = StudentParticipation(
            user_id=101,
            user_name="Dave Student",
            initial_post_word_count=250,
            reply_count=1,
            total_score=70.0,
            meets_requirements=False,
            missing_requirements=["Need 1 more reply"],
        )

        rubric_info = {
            "substantial_post_points": 60.0,
            "reply_points_each": 20.0,
            "max_replies_counted": 2,
            "min_word_count": 300,
        }

        prompt = generator._create_student_prompt(student, "Testing Discussion Topic", rubric_info)

        assert "Dave Student" in prompt
        assert "250" in prompt
        assert "1" in prompt  # reply count
        assert "70.0" in prompt
        assert "Testing Discussion Topic" in prompt
        assert "Need 1 more reply" in prompt

    def test_student_prompt_uses_ai_rubric_when_roles_present(self):
        """AI-discussion students get the 30/40/30 rubric in their prompt.

        When posts carry explicit research/restatement/reply roles the
        prompt must describe the AI Discussion rubric and the per-role
        points earned, not the legacy substantial-post + replies model.
        """
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        student = StudentParticipation(
            user_id=999,
            user_name="Ada Researcher",
            total_score=100.0,
            meets_requirements=True,
            post_details=[
                {
                    "post_number": 1,
                    "post_role": "research",
                    "word_count": 600,
                    "is_initial": True,
                    "text_snippet": "Deep research on zero-trust networks.",
                },
                {
                    "post_number": 2,
                    "post_role": "restatement",
                    "word_count": 320,
                    "is_initial": False,
                    "text_snippet": "In my own words, zero trust means...",
                },
                {
                    "post_number": 3,
                    "post_role": "reply",
                    "word_count": 180,
                    "is_initial": False,
                    "text_snippet": "Building on your point about segmentation...",
                },
            ],
        )

        prompt = generator._create_student_prompt(student, "Zero Trust", rubric_info={})

        # Describes the AI rubric, not the legacy substantial-post model.
        assert "Research" in prompt
        assert "Restatement" in prompt
        assert "Reply" in prompt
        assert "Substantial Post:" not in prompt
        # Per-role word counts and full points are surfaced.
        assert "600 words" in prompt
        assert "320 words" in prompt
        assert "180 words" in prompt
        assert "30.0/30" in prompt
        assert "40.0/40" in prompt
        assert "Ada Researcher" in prompt

    def test_batch_prompt_uses_ai_rubric_when_roles_present(self):
        """Batch feedback follows the AI rubric when the cohort is AI-graded.

        The CLI ``grade`` command defaults to batch mode, so the batch
        prompt must describe the 30/40/30 rubric and surface per-role
        word counts (Research/Restatement/Reply) just like the
        per-student path does.
        """
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        ai_student = StudentParticipation(
            user_id=42,
            user_name="Morgan Example",
            total_score=100.0,
            meets_requirements=True,
            post_details=[
                {
                    "post_number": 1,
                    "post_role": "research",
                    "word_count": 500,
                    "is_initial": True,
                    "text_snippet": "Research prompts and AI output.",
                },
                {
                    "post_number": 2,
                    "post_role": "restatement",
                    "word_count": 310,
                    "is_initial": False,
                    "text_snippet": "In my own words...",
                },
                {
                    "post_number": 3,
                    "post_role": "reply",
                    "word_count": 160,
                    "is_initial": False,
                    "text_snippet": "Replying to a classmate...",
                },
            ],
        )

        prompt = generator._create_batch_prompt([ai_student], "Zero Trust", rubric_info={})

        # Uses the AI rubric language, not the legacy substantial-post model.
        assert "Research" in prompt
        assert "Restatement" in prompt
        assert "Substantial Post:" not in prompt
        # Surfaces per-role word counts for the LLM to reference.
        assert "500/310/160 words" in prompt
        assert "Morgan Example" in prompt

    def test_batch_prompt_uses_legacy_rubric_without_roles(self):
        """Batch feedback stays on the legacy model when no AI roles exist."""
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        legacy_student = StudentParticipation(
            user_id=7,
            user_name="Legacy Larry",
            total_score=80.0,
            meets_requirements=True,
            total_posts=1,
            total_word_count=400,
            reply_count=2,
            post_details=[],
        )

        rubric_info = {
            "substantial_post": 60.0,
            "reply_points": 20.0,
            "min_replies": 2,
            "total_points": 100.0,
        }
        prompt = generator._create_batch_prompt([legacy_student], "Topic", rubric_info)

        assert "Substantial Post:" in prompt
        assert "Legacy Larry" in prompt
        assert "400 words total" in prompt

    def test_student_prompt_uses_ai_rubric_for_zero_post_student_when_profile_is_ai(self):
        """Zero-post students on an AI discussion still get the 30/40/30 prompt."""
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        student = StudentParticipation(
            user_id=102,
            user_name="Zero Zoe",
            total_score=0.0,
            meets_requirements=False,
            missing_requirements=["No posts found"],
            post_details=[],
        )

        prompt = generator._create_student_prompt(
            student,
            "Zero Trust",
            rubric_info={"grading_profile": "ai_discussion"},
        )

        assert "Research" in prompt
        assert "Restatement" in prompt
        assert "Reply" in prompt
        assert "Substantial Post:" not in prompt

    def test_batch_prompt_uses_ai_rubric_for_zero_post_cohort_when_profile_is_ai(self):
        """Batch prompts follow grading_profile even when no student posted yet."""
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        zero_student = StudentParticipation(
            user_id=103,
            user_name="Absent Alex",
            total_score=0.0,
            meets_requirements=False,
            post_details=[],
        )

        prompt = generator._create_batch_prompt(
            [zero_student],
            "Zero Trust",
            rubric_info={"grading_profile": "ai_discussion"},
        )

        assert "Research" in prompt
        assert "Restatement" in prompt
        assert "Substantial Post:" not in prompt
