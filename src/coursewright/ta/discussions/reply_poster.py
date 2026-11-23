"""Posting a drafted reply: a separate, explicit step.

Drafting (``ResponseGenerator``) only produces text.  It holds no Canvas
client and cannot post.  Posting is this one function, and it goes through
:class:`~coursewright.core.canvas.safe_write.SafeCanvasWriter`, so it is a
dry run unless the writer was built with ``execute=True`` (AGENTS.md 3.11).

The instructor reads the draft first; nothing here runs on its own.
"""

from __future__ import annotations

import html

from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteOutcome

from .response_generator import ResponseDraft


def draft_to_canvas_html(reply_text: str) -> str:
    """Turn plain reply text into escaped HTML paragraphs for Canvas."""
    paragraphs = [block.strip() for block in reply_text.split("\n\n") if block.strip()]
    return "".join(
        "<p>" + html.escape(paragraph).replace("\n", "<br>") + "</p>" for paragraph in paragraphs
    )


def post_drafted_reply(
    writer: SafeCanvasWriter,
    draft: ResponseDraft,
    course_id: int,
    topic_id: int,
    reply_text: str | None = None,
) -> WriteOutcome:
    """Post a drafted reply under the student's entry, via the safe writer.

    Args:
        writer: Decides dry run versus execute; this function never does.
        draft: The reviewed draft; ``draft.post_id`` is the entry replied to.
        course_id: Course that holds the discussion.
        topic_id: Discussion topic that holds the entry.
        reply_text: The instructor's edited text; defaults to the draft's.

    Returns:
        The writer's outcome (``executed`` is False for a dry run).
    """
    text = draft.final_content if reply_text is None else reply_text
    if not text.strip():
        raise ValueError("Refusing to post an empty reply.")
    return writer.post_discussion_reply(
        course_id, topic_id, draft.post_id, draft_to_canvas_html(text)
    )
