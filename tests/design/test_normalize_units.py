"""Offline safety checks for unit-order normalization.

Ported from COURSE_BUILDER ``tests/test_normalize_units.py``.  The script's
own backup and abort behaviour now lives in ``SafeCanvasWriter``; these
tests prove the module still honours each original guarantee through it.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import (
    BackupFailedError,
    SafeCanvasWriter,
    WriteFailedError,
)
from coursewright.design.organization.naming import NamingConvention
from coursewright.design.organization.unit_order import (
    NoModulesFoundError,
    normalize_unit_order,
    plan_unit_order,
)

MODULES = [
    {"id": 10, "name": "Unit 01 Intro"},
    {"id": 11, "name": "Welcome"},
]
MISORDERED_ITEMS = [
    {"id": 1, "title": "U01T01 Quiz", "position": 1},
    {"id": 2, "title": "U01R01 Read", "position": 2},
]


@pytest.fixture
def client():
    canvas = Mock(spec=CanvasClient)
    canvas.get_modules.return_value = MODULES
    canvas.get_module_items.return_value = MISORDERED_ITEMS
    canvas.get_module_item.side_effect = lambda course, module, item: {"id": item}
    canvas.update_module_item.return_value = {"id": 1}
    return canvas


def make_writer(client, tmp_path, *, execute):
    return SafeCanvasWriter(client, tmp_path / "backups", execute=execute)


def test_dry_run_makes_no_changes(client, tmp_path):
    writer = make_writer(client, tmp_path, execute=False)
    result = normalize_unit_order(client, writer, 123)
    assert not result.executed
    assert len(result.plan.moves) == 2
    assert len(result.outcomes) == 2
    assert not any(outcome.executed for outcome in result.outcomes)
    client.update_module_item.assert_not_called()
    client.get_module_item.assert_not_called()
    assert not (tmp_path / "backups").exists()


def test_execute_backs_up_before_each_update(client, tmp_path):
    writer = make_writer(client, tmp_path, execute=True)

    def update(course, module_id, item_id, payload):
        backups = sorted((tmp_path / "backups").glob("*.json"))
        assert len(backups) == client.update_module_item.call_count
        saved = json.loads(backups[-1].read_text(encoding="utf-8"))
        assert saved["payload"] == payload
        return {"id": item_id}

    client.update_module_item.side_effect = update
    result = normalize_unit_order(client, writer, 123)
    assert result.executed
    assert client.update_module_item.call_count == 2
    assert [call.args[3] for call in client.update_module_item.call_args_list] == [
        {"position": 1},
        {"position": 2},
    ]
    assert all(outcome.backup_path.exists() for outcome in result.outcomes)


def test_backup_failure_prevents_every_update(client, tmp_path):
    blocker = tmp_path / "backups"
    blocker.write_text("a file where the backup folder should be")
    writer = make_writer(client, tmp_path, execute=True)
    with pytest.raises(BackupFailedError):
        normalize_unit_order(client, writer, 123)
    client.update_module_item.assert_not_called()


def test_first_failed_update_stops(client, tmp_path):
    client.update_module_item.side_effect = [None]
    writer = make_writer(client, tmp_path, execute=True)
    with pytest.raises(WriteFailedError):
        normalize_unit_order(client, writer, 123)
    assert client.update_module_item.call_count == 1


def test_empty_modules_is_an_error(client):
    client.get_modules.return_value = []
    with pytest.raises(NoModulesFoundError):
        plan_unit_order(client, 123)
    client.get_module_items.assert_not_called()


def test_non_unit_module_is_never_fetched(client):
    plan_unit_order(client, 123)
    for call in client.get_module_items.call_args_list:
        assert call.args[1] == 10


def test_already_ordered_module_needs_no_moves(client):
    client.get_module_items.return_value = [
        {"id": 2, "title": "U01R01 Read", "position": 1},
        {"id": 1, "title": "U01T01 Quiz", "position": 2},
    ]
    assert plan_unit_order(client, 123).moves == []


def test_unprefixed_items_sort_to_the_end(client):
    client.get_module_items.return_value = [
        {"id": 1, "title": "Misc note", "position": 1},
        {"id": 2, "title": "U01V01 Video", "position": 2},
        {"id": 3, "title": "U01R01 Read", "position": 3},
    ]
    moves = {move.item_id: move.new_position for move in plan_unit_order(client, 123).moves}
    assert moves == {1: 3, 3: 1}


def test_letter_order_is_configuration(client):
    client.get_module_items.return_value = [
        {"id": 1, "title": "U01R01 Read", "position": 1},
        {"id": 2, "title": "U01T01 Quiz", "position": 2},
    ]
    quiz_first = NamingConvention(module_item_letter_order=("T", "R"))
    moves = {
        move.item_id: move.new_position for move in plan_unit_order(client, 1, quiz_first).moves
    }
    assert moves == {1: 2, 2: 1}


def test_unit_module_pattern_is_configuration(client):
    client.get_modules.return_value = [{"id": 20, "name": "Week 1"}]
    plan_unit_order(client, 1, NamingConvention(unit_module_pattern=r"^Week \d"))
    client.get_module_items.assert_called_once_with(1, 20)
