"""Tests for publisher module detection, naming and renaming."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteFailedError
from coursewright.design.organization.naming import NamingConvention
from coursewright.design.organization.publisher_modules import (
    apply_module_renames,
    detect_module_pattern,
    generate_new_module_name,
    plan_module_renames,
)

MODULES = [
    {"id": 1, "name": "Syllabus"},
    {"id": 2, "name": "1.1 Networks Today"},
    {"id": 3, "name": "1.2 Basic Switching"},
]


@pytest.fixture
def client():
    canvas = Mock(spec=CanvasClient)
    canvas.get_modules.return_value = MODULES
    canvas.get_module.side_effect = lambda course, module: {"id": module}
    canvas.update_module.return_value = {"id": 1}
    return canvas


def test_detect_pattern_picks_the_most_common():
    assert detect_module_pattern(MODULES) == ("dotted_numbering", {"dotted_numbering": 2})


def test_detect_pattern_none_found():
    assert detect_module_pattern([{"id": 1, "name": "Overview"}]) == (None, {})


@pytest.mark.parametrize(
    ("scheme", "expected"),
    [
        ("unit", "Unit 03 - Routing"),
        ("week", "Week 3 - Routing"),
        ("module", "Module 3 - Routing"),
        ("nonsense", "1.1 Routing"),
    ],
)
def test_generate_names(scheme, expected):
    assert generate_new_module_name("1.1 Routing", scheme, 3) == expected


def test_generate_name_extracts_number_when_missing():
    assert generate_new_module_name("Week 7 Wireless", "unit") == "Unit 07 - Wireless"
    assert generate_new_module_name("Overview", "unit") == "Unit 01 - Overview"


def test_scheme_labels_are_configuration():
    naming = NamingConvention(module_scheme_labels={"unit": "Chapter"})
    assert generate_new_module_name("Routing", "unit", 4, naming) == "Chapter 04 - Routing"


def test_plan_skips_intro_modules_and_numbers_the_rest(client):
    plan = plan_module_renames(client, 5)
    assert [entry.new_name for entry in plan.entries] == [
        "Syllabus",
        "Unit 01 - Networks Today",
        "Unit 02 - Basic Switching",
    ]
    assert [entry.renamed for entry in plan.entries] == [False, True, True]


def test_plan_respects_selection(client):
    plan = plan_module_renames(client, 5, modules_to_rename=["1.2 Basic Switching"])
    assert [entry.renamed for entry in plan.entries] == [False, False, True]
    assert plan.skipped_count == 1


def test_intro_keywords_are_configuration(client):
    naming = NamingConvention(intro_module_keywords=())
    plan = plan_module_renames(client, 5, naming=naming)
    assert plan.entries[0].renamed


def test_dry_run_writes_nothing(client, tmp_path):
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=False)
    result = apply_module_renames(plan_module_renames(client, 5), writer)
    assert not result.executed
    assert len(result.outcomes) == 2
    client.update_module.assert_not_called()
    client.get_module.assert_not_called()
    assert not (tmp_path / "backups").exists()


def test_execute_backs_up_then_renames(client, tmp_path):
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    result = apply_module_renames(plan_module_renames(client, 5), writer)
    assert result.executed
    assert client.update_module.call_count == 2
    client.update_module.assert_any_call(5, 2, {"name": "Unit 01 - Networks Today", "position": 2})
    saved = [json.loads(path.read_text()) for path in (tmp_path / "backups").glob("*.json")]
    assert len(saved) == 2


def test_refused_rename_raises_and_stops(client, tmp_path):
    client.update_module.return_value = None
    writer = SafeCanvasWriter(client, tmp_path / "backups", execute=True)
    with pytest.raises(WriteFailedError):
        apply_module_renames(plan_module_renames(client, 5), writer)
    assert client.update_module.call_count == 1
