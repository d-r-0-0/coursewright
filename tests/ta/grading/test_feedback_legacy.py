"""Restored discussorator feedback regressions with synthetic data."""

from unittest.mock import Mock, patch

import pytest

from coursewright.core.canvas.models import StudentParticipation
from coursewright.core.llm import cost_tracker
from coursewright.core.llm.client import LLMClient, LLMConfig, LLMProvider, LLMResponse
from coursewright.ta.grading.feedback_generator import FeedbackGenerator


@pytest.fixture(autouse=True)
def isolate_usage_records(monkeypatch):
    monkeypatch.setattr(cost_tracker, "record_usage", lambda **fields: None)


class TestFeedbackGenerator:
    """Test feedback generation functionality"""

    def test_feedback_generator_creation(self):
        """Test creating feedback generator"""
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        assert generator.llm_client == mock_client

    def test_generate_student_feedback(self):
        """Test generating feedback for a student"""
        # Create mock LLM client
        mock_client = Mock(spec=LLMClient)
        mock_response = LLMResponse(
            content=(
                "Great work on your discussion! "
                "Your thoughtful 350-word post shows deep engagement."
            ),
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            tokens_used=50,
        )
        mock_client.generate_sync.return_value = mock_response

        # Create feedback generator
        generator = FeedbackGenerator(mock_client)

        # Create student participation
        student = StudentParticipation(
            user_id=101,
            user_name="Alice Student",
            initial_post=True,
            initial_post_word_count=350,
            reply_count=2,
            total_word_count=450,
            total_posts=3,
            total_score=100.0,
            meets_requirements=True,
        )

        # Generate feedback
        rubric_info = {
            "substantial_post_points": 60.0,
            "reply_points_each": 20.0,
            "max_replies_counted": 2,
            "min_word_count": 300,
        }

        feedback = generator.generate_student_feedback(
            student=student,
            discussion_context="Introduction to Machine Learning",
            rubric_info=rubric_info,
        )

        # Verify
        assert (
            feedback
            == "Great work on your discussion! Your thoughtful 350-word post shows deep engagement."
        )
        assert mock_client.generate_sync.called

    def test_fallback_feedback_success(self):
        """Test fallback feedback for successful student"""
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        student = StudentParticipation(
            user_id=101,
            user_name="Bob Student",
            total_posts=3,
            meets_requirements=True,
            missing_requirements=[],
        )

        feedback = generator._create_fallback_feedback(student)

        assert "Great work" in feedback
        assert "Bob Student" not in feedback  # Name not included in fallback
        assert "3" in feedback

    def test_fallback_feedback_missing_requirements(self):
        """Test fallback feedback for student missing requirements"""
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)

        student = StudentParticipation(
            user_id=101,
            user_name="Charlie Student",
            total_posts=1,
            meets_requirements=False,
            missing_requirements=["Need 2 replies", "Main post too short"],
        )

        feedback = generator._create_fallback_feedback(student)

        assert "Thank you for your participation" in feedback
        assert "Need 2 replies" in feedback
        assert "Main post too short" in feedback

class TestIntegration:
    """Integration tests for LLM feedback"""

    @patch("httpx.Client")
    @patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-key"})  # pragma: allowlist secret
    def test_full_feedback_generation_flow(self, mock_httpx):
        """Test complete feedback generation flow"""
        # Mock HTTP response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {"content": "Excellent analysis of the topic!"},
                    "finish_reason": "stop",
                }
            ],
            "model": "gpt-4o-mini",
            "usage": {"total_tokens": 45},
        }

        mock_client_instance = Mock()
        mock_client_instance.post.return_value = mock_response
        mock_httpx.return_value = mock_client_instance

        # Create LLM client and generator
        config = LLMConfig(
            provider=LLMProvider.OPENAI,
            model="gpt-4o-mini",
            api_key="synthetic-key",  # pragma: allowlist secret
        )
        client = LLMClient(config)
        generator = FeedbackGenerator(client)

        # Create student
        student = StudentParticipation(
            user_id=101,
            user_name="Eve Student",
            initial_post_word_count=400,
            reply_count=3,
            total_score=100.0,
            meets_requirements=True,
        )

        rubric_info = {
            "substantial_post_points": 60.0,
            "reply_points_each": 20.0,
            "max_replies_counted": 2,
            "min_word_count": 300,
        }

        # Generate feedback
        feedback = generator.generate_student_feedback(student, "AI Ethics Discussion", rubric_info)

        assert feedback == "Excellent analysis of the topic!"
