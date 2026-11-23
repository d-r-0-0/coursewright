"""Tests for the feedback generator's use of the bundled prompt library.

The feedback generator used to read prompt files that sat next to its own
module.  Prompts now live in ``core/llm/prompts`` and are loaded through the
prompt library, so these tests prove the generator finds them there and still
falls back to its inline prompts when a bundled one is missing.
"""

from types import SimpleNamespace
from unittest.mock import Mock

from coursewright.core.canvas.models import StudentParticipation
from coursewright.core.llm.prompt_library import load_prompt
from coursewright.ta.grading import feedback_generator
from coursewright.ta.grading.feedback_generator import FeedbackGenerator


def make_generator(llm_response_text: str = "") -> tuple[FeedbackGenerator, Mock]:
    llm_client = Mock()
    llm_client.generate_sync.return_value = SimpleNamespace(content=llm_response_text)
    return FeedbackGenerator(llm_client), llm_client


def make_student(user_id: int, name: str) -> StudentParticipation:
    return StudentParticipation(
        user_id=user_id,
        user_name=name,
        total_posts=3,
        total_word_count=450,
        reply_count=2,
        total_score=90.0,
    )


def test_system_prompt_comes_from_the_bundled_prompt_library():
    generator, _ = make_generator()

    assert generator._create_system_prompt() == load_prompt("FEEDBACK_SYSTEM.md")


def test_system_prompt_falls_back_inline_when_bundled_prompt_is_missing(monkeypatch):
    def missing_prompt(prompt_file_name: str) -> str:
        raise FileNotFoundError(prompt_file_name)

    monkeypatch.setattr(feedback_generator, "load_prompt", missing_prompt)
    generator, _ = make_generator()

    assert generator._create_system_prompt().startswith("You are a supportive college instructor")


def test_batch_request_uses_the_graderator_prompt_as_system_prompt():
    students = [make_student(1, "Ada Example"), make_student(2, "Ben Example")]
    reply = "Ada Example\t90\tGreat work.\nBen Example\t90\tNice reply."
    generator, llm_client = make_generator(reply)

    feedback = generator.generate_batch_feedback_single_request(
        students, "Week 1", {"substantial_post": 60, "reply_points": 20}
    )

    sent_system_prompt = llm_client.generate_sync.call_args.kwargs["system_prompt"]
    assert sent_system_prompt == load_prompt("DISCUSSION_GRADERATOR.md")
    assert feedback == {1: "Great work.", 2: "Nice reply."}
