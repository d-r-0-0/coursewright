"""Posting a drafted reply is explicit: no execute flag, no write.

These tests prove acceptance item 5 of the card.  Drafting never reaches
Canvas, and posting is a dry run unless the safe writer was built with
``execute=True``.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import Mock

import pytest
import requests

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter
from coursewright.ta.discussions.reply_poster import draft_to_canvas_html, post_drafted_reply
from coursewright.ta.discussions.response_generator import ResponseDraft, ResponseGenerator


def make_response(status_code, payload=None):
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(payload if payload is not None else {}).encode()
    return response


@pytest.fixture
def session():
    return Mock()


@pytest.fixture
def client(session):
    canvas = CanvasClient("https://canvas.example.edu", "synthetic-token")
    canvas._session = session
    canvas._rate_limit_delay = 0
    return canvas


@pytest.fixture
def draft():
    return ResponseDraft(
        post_id=77,
        student_name="Alice",
        discussion_title="Topic",
        post_summary="A summary",
        draft_content="Thanks, Alice.\n\nWhat about <edge> cases?",
        final_content="Thanks, Alice.\n\nWhat about <edge> cases?",
        generated_date=datetime(2026, 1, 2),
        word_count=7,
    )


def test_generating_a_draft_makes_no_canvas_call(session):
    llm = Mock()
    llm.generate_sync.return_value = Mock(content="A kind reply.")
    entry = Mock(id=5, user_name="Alice", message="<p>Hi</p>")

    generated = ResponseGenerator(llm).generate_response(entry, "Topic")

    assert generated.final_content == "A kind reply."
    session.request.assert_not_called()


def test_posting_defaults_to_dry_run_and_sends_nothing(client, session, tmp_path, draft):
    writer = SafeCanvasWriter(client, tmp_path / "backups")  # no execute argument

    outcome = post_drafted_reply(writer, draft, course_id=1, topic_id=2)

    assert outcome.executed is False
    assert outcome.operation == "post_discussion_reply"
    assert outcome.target == "course 1 discussion topic 2 entry 77"
    session.request.assert_not_called()
    assert not (tmp_path / "backups").exists()


def test_posting_with_execute_backs_up_then_posts_the_reply(client, session, tmp_path, draft):
    session.request.return_value = make_response(201, {"id": 901})
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)

    outcome = post_drafted_reply(writer, draft, course_id=1, topic_id=2)

    assert outcome.executed is True
    assert outcome.result == {"id": 901}
    assert outcome.backup_path.exists()
    method, url = session.request.call_args.args[:2]
    assert method == "POST"
    assert url.endswith("/api/v1/courses/1/discussion_topics/2/entries/77/replies")
    sent_message = session.request.call_args.kwargs["data"]["message"]
    assert sent_message == "<p>Thanks, Alice.</p><p>What about &lt;edge&gt; cases?</p>"


def test_instructor_edit_replaces_the_draft_text(client, session, tmp_path, draft):
    writer = SafeCanvasWriter(client, tmp_path / "backups")

    outcome = post_drafted_reply(writer, draft, 1, 2, reply_text="Edited reply")

    assert outcome.payload == {"message": "<p>Edited reply</p>"}


def test_empty_reply_is_refused(client, tmp_path, draft):
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)

    with pytest.raises(ValueError, match="empty"):
        post_drafted_reply(writer, draft, 1, 2, reply_text="   ")


def test_html_conversion_escapes_and_keeps_line_breaks():
    assert draft_to_canvas_html("a & b\nc") == "<p>a &amp; b<br>c</p>"
