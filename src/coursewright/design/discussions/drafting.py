"""Draft discussion starter posts from class transcripts.

This is the *drafting* half of Phase 3.  It talks to an LLM through
``coursewright.core.llm`` and to nothing else: it never touches Canvas.
Creating the drafted topics in a course is a separate function
(`coursewright.design.discussions.creation.create_discussions`), so the
instructor can read and edit every draft before anything is written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from coursewright.core.llm.client import LLMResponse
from coursewright.design.discussions.templates import fill_template, load_template

DEFAULT_INSTRUCTOR_GUIDANCE = "None. Use a friendly, plain tone."

# A model sometimes wraps HTML in a Markdown fence despite being told not to.
_FENCE_PATTERN = re.compile(r"^```[a-zA-Z]*\s*\n(.*?)\n```\s*$", re.DOTALL)


class DraftingError(Exception):
    """The model's answer could not be used as a starter post."""


class TextGenerator(Protocol):
    """What drafting needs from an LLM client (``LLMClient`` satisfies it)."""

    def generate_sync(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


@dataclass(frozen=True)
class DiscussionDraft:
    """One drafted starter post, ready for the instructor to review."""

    unit_label: str
    title: str
    message_html: str


def clean_model_html(model_text: str) -> str:
    """Strip whitespace and a stray Markdown code fence from a model answer."""
    cleaned = model_text.strip()
    fenced = _FENCE_PATTERN.match(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    return cleaned


def draft_discussion_starter_post(
    llm_client: TextGenerator,
    transcript_text: str,
    unit_label: str,
    *,
    title: str | None = None,
    instructor_guidance: str = DEFAULT_INSTRUCTOR_GUIDANCE,
    max_tokens: int = 1200,
) -> DiscussionDraft:
    """Ask the model for one starter post based on *transcript_text*.

    Args:
        llm_client: Anything with ``generate_sync`` (an ``LLMClient``).
        transcript_text: Plain text of what was said in class.
        unit_label: Short label for the unit, such as ``"Unit 03"``.
        title: Discussion title; defaults to ``"<unit_label> Discussion"``.
        instructor_guidance: Tone or content notes for the model.
        max_tokens: Completion budget for the draft.

    Raises:
        ValueError: the transcript or unit label is empty.
        DraftingError: the model returned nothing usable.
    """
    if not transcript_text.strip():
        raise ValueError("A transcript is required to draft a starter post.")
    if not unit_label.strip():
        raise ValueError("A unit label is required to draft a starter post.")

    user_prompt = fill_template(
        load_template("draft_user.md"),
        {
            "unit_label": unit_label,
            "instructor_guidance": instructor_guidance,
            "transcript": transcript_text.strip(),
        },
    )
    response = llm_client.generate_sync(
        user_prompt,
        system_prompt=load_template("draft_system.md"),
        max_tokens=max_tokens,
    )
    message_html = clean_model_html(response.content)
    if not message_html:
        raise DraftingError(f"The model returned an empty draft for {unit_label}.")
    return DiscussionDraft(
        unit_label=unit_label,
        title=title or f"{unit_label} Discussion",
        message_html=message_html,
    )
