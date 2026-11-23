"""Restored discussorator feedback regressions with synthetic data."""

from unittest.mock import Mock

from coursewright.core.canvas.models import StudentParticipation
from coursewright.core.llm.client import LLMClient, LLMProvider, LLMResponse
from coursewright.ta.grading.feedback_generator import FeedbackGenerator


class TestRoleVerification:
    """Test the LLM second-opinion role-mapping path."""

    def _make_student(self) -> StudentParticipation:
        student = StudentParticipation(
            user_id=42,
            user_name="Role Check Student",
            total_posts=3,
        )
        student.post_details = [
            {
                "post_number": 1,
                "word_count": 305,
                "post_role": "research",
                "parent_relationship": "top_level",
                "text_snippet": "Initial top-level post about phishing.",
            },
            {
                "post_number": 2,
                "word_count": 600,
                "post_role": "restatement",
                "parent_relationship": "self_reply",
                "text_snippet": "Reply to my own post going deeper.",
            },
            {
                "post_number": 3,
                "word_count": 160,
                "post_role": "reply",
                "parent_relationship": "classmate_reply",
                "text_snippet": "Reply to a classmate.",
            },
        ]
        return student

    def test_verify_role_assignment_parses_clean_json(self):
        mock_client = Mock(spec=LLMClient)
        mock_client.generate_sync.return_value = LLMResponse(
            content=(
                '{"research": 2, "restatement": 1, "reply": 3, '
                '"rationale": "Post 2 is the real research post."}'
            ),
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            tokens_used=40,
        )
        generator = FeedbackGenerator(mock_client)

        mapping = generator.verify_role_assignment(self._make_student())

        assert mapping == {
            "research": 2,
            "restatement": 1,
            "reply": 3,
            "rationale": "Post 2 is the real research post.",
        }

    def test_verify_role_assignment_strips_code_fences(self):
        mock_client = Mock(spec=LLMClient)
        mock_client.generate_sync.return_value = LLMResponse(
            content=(
                "```json\n"
                '{"research": 1, "restatement": 2, "reply": null, '
                '"rationale": "No classmate reply found."}\n'
                "```"
            ),
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            tokens_used=40,
        )
        generator = FeedbackGenerator(mock_client)

        mapping = generator.verify_role_assignment(self._make_student())

        assert mapping is not None
        assert mapping["research"] == 1
        assert mapping["restatement"] == 2
        assert mapping["reply"] is None

    def test_verify_role_assignment_returns_none_on_garbage(self):
        mock_client = Mock(spec=LLMClient)
        mock_client.generate_sync.return_value = LLMResponse(
            content="Sorry, I cannot help with that.",
            model="gpt-4o-mini",
            provider=LLMProvider.OPENAI,
            tokens_used=10,
        )
        generator = FeedbackGenerator(mock_client)

        mapping = generator.verify_role_assignment(self._make_student())

        assert mapping is None

    def test_verify_role_assignment_returns_none_on_exception(self):
        mock_client = Mock(spec=LLMClient)
        mock_client.generate_sync.side_effect = RuntimeError("network down")
        generator = FeedbackGenerator(mock_client)

        mapping = generator.verify_role_assignment(self._make_student())

        assert mapping is None
        assert any("role-verify" in err for err in generator.errors)

    def test_verify_role_assignment_returns_none_when_no_posts(self):
        mock_client = Mock(spec=LLMClient)
        generator = FeedbackGenerator(mock_client)
        student = StudentParticipation(user_id=1, user_name="No Posts")
        student.post_details = []

        mapping = generator.verify_role_assignment(student)

        assert mapping is None
        assert not mock_client.generate_sync.called
