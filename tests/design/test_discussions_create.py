"""Creating discussions: dry run by default, execute writes backup-first."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import (
    BackupFailedError,
    SafeCanvasWriter,
)
from coursewright.design.discussions.creation import (
    build_discussion_topic_payload,
    create_discussions,
    default_discussion_for_module,
    select_unit_modules,
)
from coursewright.design.discussions.drafting import DiscussionDraft

COURSE_ID = 101
MODULES = [
    {"id": 1, "name": "Unit 01 - Basics"},
    {"id": 2, "name": "Unit 02 - More"},
    {"id": 3, "name": "Unit 03 - Loops"},
    {"id": 15, "name": "Unit 15 - Late"},
    {"id": 99, "name": "Course Wrap-up"},
]


@pytest.fixture
def client():
    canvas = Mock(spec=CanvasClient)
    canvas.create_discussion_topic.side_effect = lambda course_id, data: {
        "id": 500 + len(data["title"])
    }
    canvas.create_module_item.return_value = {"id": 9}
    return canvas


def make_writer(client, tmp_path, *, execute):
    return SafeCanvasWriter(client, tmp_path / "backups", execute=execute)


def test_select_unit_modules_picks_odd_units_up_to_thirteen():
    assert select_unit_modules(MODULES) == ["Unit 01 - Basics", "Unit 03 - Loops"]


def test_default_titles_and_prompts():
    title, message = default_discussion_for_module("Unit 01 - Basics")
    assert title == "U01D01 - Getting to Know You" and "Introduce yourself" in message
    title, message = default_discussion_for_module("Unit 3 - Loops")
    assert title == "U3D01 - Unit 3 - Loops Discussion" and "Reflect" in message
    assert default_discussion_for_module("Wrap-up")[0] == "Wrap-up Discussion"


def test_payload_is_graded_threaded_and_unpublished():
    payload = build_discussion_topic_payload("T", "<p>m</p>", points=50)
    assert payload["published"] is False
    assert payload["require_initial_post"] is True
    assert payload["assignment"]["points_possible"] == 50
    assert payload["assignment"]["submission_types"] == ["discussion_topic"]


# ----- dry run -----------------------------------------------------------


def test_dry_run_makes_no_canvas_call_and_writes_no_backup(client, tmp_path):
    writer = make_writer(client, tmp_path, execute=False)
    report = create_discussions(writer, COURSE_ID, MODULES, ["Unit 01 - Basics", "Unit 03 - Loops"])
    assert report.executed is False
    assert report.discussions_planned == 2
    assert report.discussions_created == 0 and report.discussions_added == 0
    assert report.modules_skipped == 3
    assert not report.errors
    assert client.method_calls == []
    assert not (tmp_path / "backups").exists()
    item_payload = report.module_item_outcomes[0].payload
    assert item_payload == {"type": "Discussion", "content_id": None, "position": 1}
    assert all(not o.executed for o in report.topic_outcomes)


# ----- execute -----------------------------------------------------------


def test_execute_creates_topic_then_module_item_with_backups(client, tmp_path):
    writer = make_writer(client, tmp_path, execute=True)
    draft = DiscussionDraft("Unit 03", "U03D01 - Loops", "<p>Drafted</p>")
    report = create_discussions(
        writer,
        COURSE_ID,
        MODULES,
        ["Unit 03 - Loops"],
        drafts_by_module_name={"Unit 03 - Loops": draft},
        points=25,
    )
    assert report.executed and not report.errors
    assert report.discussions_created == 1 and report.discussions_added == 1

    topic_args = client.create_discussion_topic.call_args.args
    assert topic_args[0] == COURSE_ID
    assert topic_args[1]["title"] == "U03D01 - Loops"
    assert topic_args[1]["message"] == "<p>Drafted</p>"
    assert topic_args[1]["assignment"]["points_possible"] == 25

    topic_id = 500 + len("U03D01 - Loops")
    client.create_module_item.assert_called_once_with(
        COURSE_ID, 3, {"type": "Discussion", "content_id": topic_id, "position": 1}
    )
    backups = sorted((tmp_path / "backups").glob("*.json"))
    assert len(backups) == 2
    assert json.loads(backups[0].read_text())["payload"]["title"] == "U03D01 - Loops"


def test_execute_records_refused_topic_and_continues(client, tmp_path):
    client.create_discussion_topic.side_effect = [None, {"id": 7}]
    writer = make_writer(client, tmp_path, execute=True)
    report = create_discussions(writer, COURSE_ID, MODULES, ["Unit 01 - Basics", "Unit 03 - Loops"])
    assert len(report.errors) == 1 and "Unit 01 - Basics" in report.errors[0]
    assert report.discussions_created == 1 and report.discussions_added == 1
    client.create_module_item.assert_called_once()


def test_execute_records_module_item_failure(client, tmp_path):
    client.create_module_item.return_value = None
    writer = make_writer(client, tmp_path, execute=True)
    report = create_discussions(writer, COURSE_ID, MODULES, ["Unit 01 - Basics"])
    assert report.discussions_created == 1 and report.discussions_added == 0
    assert "Created but failed to add to module" in report.errors[0]


def test_execute_aborts_when_backup_fails(client, tmp_path):
    blocker = tmp_path / "backups"
    blocker.write_text("a file where the folder should be")
    writer = make_writer(client, tmp_path, execute=True)
    with pytest.raises(BackupFailedError):
        create_discussions(writer, COURSE_ID, MODULES, ["Unit 01 - Basics", "Unit 03 - Loops"])
    client.create_discussion_topic.assert_not_called()
    client.create_module_item.assert_not_called()
