"""Drafting starter posts: LLM only, never Canvas; templates stay placeholders."""

from __future__ import annotations

import hashlib
import re
from unittest.mock import Mock

import pytest

from coursewright.core.llm.client import LLMProvider, LLMResponse
from coursewright.design.discussions.drafting import (
    DraftingError,
    clean_model_html,
    draft_discussion_starter_post,
)
from coursewright.design.discussions.templates import (
    fill_template,
    find_placeholders,
    list_template_names,
    load_template,
)

FORBIDDEN_TEMPLATE_TOKEN_DIGESTS = {
    "33843d5ed74d74a0c386b3e34f03b7f1f9e0471f3861a633a51abd9e04176d2e",  # pragma: allowlist secret
    "a89eccf183d31be9573e59a22690c11c2b65137ea8fbf6c6fe196b096986c10c",  # pragma: allowlist secret
}


def fake_llm(content: str) -> Mock:
    client = Mock()
    client.generate_sync.return_value = LLMResponse(
        content=content, model="synthetic-model", provider=LLMProvider.OPENAI
    )
    return client


def test_draft_sends_transcript_and_returns_draft():
    llm = fake_llm("<p>Frame.</p><ol><li>Q1</li></ol>")
    draft = draft_discussion_starter_post(llm, "We covered loops today.", "Unit 03")
    assert draft.unit_label == "Unit 03"
    assert draft.title == "Unit 03 Discussion"
    assert draft.message_html == "<p>Frame.</p><ol><li>Q1</li></ol>"
    prompt = llm.generate_sync.call_args.args[0]
    assert "We covered loops today." in prompt
    assert "Unit 03" in prompt
    assert "{{" not in prompt
    assert "transcript" in llm.generate_sync.call_args.kwargs["system_prompt"]


def test_draft_uses_given_title_and_guidance():
    llm = fake_llm("<p>x</p>")
    draft = draft_discussion_starter_post(
        llm, "text", "Unit 05", title="U05D01 - Loops", instructor_guidance="Be brief."
    )
    assert draft.title == "U05D01 - Loops"
    assert "Be brief." in llm.generate_sync.call_args.args[0]


def test_draft_strips_markdown_fence():
    assert clean_model_html("```html\n<p>Hi</p>\n```") == "<p>Hi</p>"
    assert clean_model_html("  <p>Hi</p>\n") == "<p>Hi</p>"


@pytest.mark.parametrize("transcript, label", [("  ", "Unit 01"), ("text", " ")])
def test_draft_rejects_empty_input_without_calling_the_model(transcript, label):
    llm = fake_llm("<p>x</p>")
    with pytest.raises(ValueError):
        draft_discussion_starter_post(llm, transcript, label)
    llm.generate_sync.assert_not_called()


def test_draft_empty_model_answer_is_an_error():
    with pytest.raises(DraftingError):
        draft_discussion_starter_post(fake_llm("```html\n\n```"), "text", "Unit 01")


def test_draft_never_needs_a_canvas_client():
    # The drafting module must not import the Canvas client at all.
    import coursewright.design.discussions.drafting as drafting

    assert "canvas" not in " ".join(vars(drafting))


# ----- templates ---------------------------------------------------------


def test_fill_template_replaces_all_and_repeats():
    assert fill_template("{{a}} and {{a}} and {{b_2}}", {"a": "1", "b_2": "2"}) == "1 and 1 and 2"


def test_fill_template_missing_value_is_loud():
    with pytest.raises(KeyError, match="b"):
        fill_template("{{a}} {{b}}", {"a": "1"})


def test_find_placeholders_in_order_without_duplicates():
    assert find_placeholders("{{b}} {{a}} {{b}}") == ["b", "a"]


def test_load_template_rejects_paths():
    with pytest.raises(FileNotFoundError):
        load_template("../drafting.py")


def test_bundled_templates_are_placeholders_only():
    names = list_template_names()
    assert {"unit_discussion.md", "intro_discussion.md", "discussion_rubric.md"} <= set(names)
    for name in names:
        text = load_template(name)
        if name != "draft_system.md":  # a fixed system prompt takes no values
            assert find_placeholders(text), name
        # Strip the markers and markup; only generic structural words may remain.
        prose = re.sub(r"\{\{[a-z0-9_]+\}\}|<[^>]+>|[#*]", " ", text)
        for pattern in (
            re.compile(r"\b[a-z]{2,4}[ -]?\d{3}\b", re.IGNORECASE),
            re.compile(r"\b(?:fall|spring|summer|winter) \d{2,4}\b", re.IGNORECASE),
            re.compile(r"@"),
            re.compile(r"https?://", re.IGNORECASE),
        ):
            assert pattern.search(prose) is None, (name, pattern.pattern)

        for token in re.findall(r"\b\w+\b", prose):
            token_digest = hashlib.sha256(token.casefold().encode()).hexdigest()
            assert token_digest not in FORBIDDEN_TEMPLATE_TOKEN_DIGESTS, (name, token)
