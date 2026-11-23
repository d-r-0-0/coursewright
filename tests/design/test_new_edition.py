"""Tests for sorting a flat publisher dump into unit modules."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteFailedError
from coursewright.design.organization.naming import NamingConvention
from coursewright.design.organization.new_edition import (
    NewEditionLayout,
    apply_new_edition,
    classify,
    plan_new_edition,
)

LAYOUT = NewEditionLayout(
    unit_titles={1: "Unit 01: Foundations", 2: "Unit 02: Systems"},
    resources_title="Start Here: Resources",
    watch_unit={"Career Paths": 1, "Orientation Tour": None},
    remove_prefixes=("Study It", "Reflection"),
)

RAW_ITEMS = [
    {"id": 1, "title": "Quiz: Module 01 Foundations"},
    {"id": 2, "title": "Chapter 1: Foundations"},
    {"id": 3, "title": "Read: Module 1 Foundations"},
    {"id": 4, "title": "Simulation 1-2: Build a Thing"},
    {"id": 5, "title": "Simulation 1-1: Break a Thing"},
    {"id": 6, "title": "Study It: Module 1"},
    {"id": 7, "title": "Watch: Career Paths Overview"},
    {"id": 8, "title": "Watch: Orientation Tour"},
    {"id": 9, "title": "Watch: Unmapped Topic"},
    {"id": 10, "title": "Live Virtual Machine Lab 1-1: Old"},
    {"id": 11, "title": "Lab Setup Guide"},
    {"id": 12, "title": "Expert Virtual Lab: Hard"},
    {"id": 13, "title": "Virtual Lab 2-1, Guided: Network"},
]


@pytest.fixture
def client():
    canvas = Mock(spec=CanvasClient)
    canvas.get_module_items.return_value = RAW_ITEMS
    canvas.get_modules.return_value = [{"id": 900, "name": "Unit 01: Foundations"}]
    canvas.get_module_item.side_effect = lambda course, module, item: {"id": item}
    canvas.create_module.side_effect = [{"id": 901}, {"id": 902}]
    canvas.move_module_item.return_value = {"id": 1}
    canvas.update_module_item.return_value = {"id": 1}
    canvas.delete_module_item.return_value = True
    return canvas


def make_writer(client, tmp_path, *, execute):
    return SafeCanvasWriter(client, tmp_path / "backups", execute=execute)


def test_classify_actions():
    assert classify("Study It: x", LAYOUT) == {"action": "remove"}
    assert classify("Lab Setup Guide", LAYOUT) == {"action": "course"}
    assert classify("Watch: Orientation Tour", LAYOUT) == {"action": "course"}
    assert classify("Watch: Unmapped", LAYOUT)["action"] == "supplemental"
    assert classify("Expert Virtual Lab: X", LAYOUT)["cat"] == "xlab"


def test_plan_orders_and_titles(client):
    plan = plan_new_edition(client, 5, 800, LAYOUT)
    assert [(p.position, p.new_title) for p in plan.units[1]] == [
        (1, "U01R01: Chapter — Foundations"),
        (2, "U01R02: Read — Foundations"),
        (3, "U01V02: Watch — Career Paths Overview"),
        (4, "U01A01: Simulation 1-1 — Break a Thing"),
        (5, "U01A02: Simulation 1-2 — Build a Thing"),
        (6, "U01T01: Quiz — Module 01"),
    ]
    assert plan.units[2][0].new_title == ("U02A01: Virtual Lab 2-1 (Guided) — Network")
    assert [item["id"] for item in plan.removes] == [6, 10]
    assert [item["id"] for item in plan.course_items] == [8, 11]
    assert [item["id"] for item in plan.holds] == [9, 12]


def test_prefix_format_and_separator_are_configuration(client):
    naming = NamingConvention(
        item_prefix_template="{letter}{unit}.{sequence}", descriptor_separator=" - "
    )
    plan = plan_new_edition(client, 5, 800, LAYOUT, naming)
    assert plan.units[1][0].new_title == "R1.1: Chapter - Foundations"


def test_dry_run_writes_nothing(client, tmp_path):
    plan = plan_new_edition(client, 5, 800, LAYOUT)
    result = apply_new_edition(plan, client, make_writer(client, tmp_path, execute=False))
    assert not result.executed
    assert result.operations_applied == 6 + 1 + 2 + 2
    client.create_module.assert_not_called()
    client.update_module_item.assert_not_called()
    client.move_module_item.assert_not_called()
    client.delete_module_item.assert_not_called()
    client.get_module_item.assert_not_called()
    assert not (tmp_path / "backups").exists()


def test_execute_creates_modules_moves_and_deletes(client, tmp_path):
    plan = plan_new_edition(client, 5, 800, LAYOUT)
    result = apply_new_edition(plan, client, make_writer(client, tmp_path, execute=True))
    assert result.executed
    created = [call.args[1]["name"] for call in client.create_module.call_args_list]
    assert created == ["Start Here: Resources", "Unit 02: Systems"]
    first_move = client.update_module_item.call_args_list[0]
    assert first_move.args[:3] == (5, 800, 2)
    assert first_move.args[3] == {
        "module_id": 900,
        "position": 1,
        "title": "U01R01: Chapter — Foundations",
    }
    assert [call.args[2] for call in client.delete_module_item.call_args_list] == [6, 10]
    resource_moves = client.move_module_item.call_args_list
    assert [call.args[2:] for call in resource_moves] == [(8, 901, 1), (11, 901, 2)]
    unit_two_move = client.update_module_item.call_args_list[-1]
    assert unit_two_move.args[3]["module_id"] == 902
    assert len(list((tmp_path / "backups").glob("*.json"))) == 2 + 6 + 1 + 2 + 2


def test_limit_stops_early(client, tmp_path):
    plan = plan_new_edition(client, 5, 800, LAYOUT)
    result = apply_new_edition(plan, client, make_writer(client, tmp_path, execute=True), limit=3)
    assert result.stopped_at_limit
    assert client.update_module_item.call_count == 3
    client.delete_module_item.assert_not_called()


def test_refused_move_raises_and_stops(client, tmp_path):
    client.update_module_item.return_value = None
    plan = plan_new_edition(client, 5, 800, LAYOUT)
    with pytest.raises(WriteFailedError):
        apply_new_edition(plan, client, make_writer(client, tmp_path, execute=True))
    assert client.update_module_item.call_count == 1
    client.delete_module_item.assert_not_called()
