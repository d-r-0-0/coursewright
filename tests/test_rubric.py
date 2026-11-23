"""Tests for the rubric loader and the bundled rubric files."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from coursewright.design.standards import (
    RubricError,
    list_rubric_ids,
    load_rubric,
    load_rubric_file,
)
from coursewright.ui.cli.main import app

MINIMAL_RUBRIC = """
[rubric]
id = "{rubric_id}"
name = "Local rubric"
source_url = "https://example.org/rubric"
text_included = true

[[section]]
id = "only"
name = "Only section"

[[standard]]
id = "A"
section = "only"
text = "Workspace wording."
"""


def write_workspace_rubric(workspace: Path, rubric_id: str, body: str) -> Path:
    rubrics_folder = workspace / "rubrics"
    rubrics_folder.mkdir(parents=True, exist_ok=True)
    rubric_path = rubrics_folder / f"{rubric_id}.toml"
    rubric_path.write_text(body, encoding="utf-8")
    return rubric_path


def test_default_rubric_is_oscqr_with_fifty_full_text_standards() -> None:
    rubric = load_rubric()
    assert rubric.id == "oscqr"
    assert rubric.license == "CC-BY-4.0"
    assert rubric.text_included
    assert [standard.id for standard in rubric.standards] == [str(n) for n in range(1, 51)]
    assert len(rubric.sections) == 6
    assert all(rubric.standards_in_section(section.id) for section in rubric.sections)
    assert all(s.url.startswith("https://oscqr.suny.edu/standard") for s in rubric.standards)


def test_quality_matters_ships_without_any_standard_text() -> None:
    """Guards the licence decision: QM wording must never be bundled."""
    rubric = load_rubric("qm-he-7")
    assert not rubric.text_included
    assert len(rubric.standards) == 44
    assert all(standard.text == "" for standard in rubric.standards)
    assert all(standard.points is None for standard in rubric.standards)
    assert all("qualitymatters.org" in standard.url for standard in rubric.standards)


def test_workspace_rubric_overrides_bundled_one(tmp_path: Path) -> None:
    write_workspace_rubric(tmp_path, "oscqr", MINIMAL_RUBRIC.format(rubric_id="oscqr"))
    rubric = load_rubric("oscqr", tmp_path)
    assert rubric.find_standard("A").text == "Workspace wording."
    assert not rubric.origin.startswith("bundled:")


def test_workspace_rubrics_are_listed_beside_bundled(tmp_path: Path) -> None:
    write_workspace_rubric(tmp_path, "local", MINIMAL_RUBRIC.format(rubric_id="local"))
    assert list_rubric_ids(tmp_path) == ["local", "oscqr", "qm-he-7"]
    assert list_rubric_ids(tmp_path / "missing") == ["oscqr", "qm-he-7"]


def test_unknown_rubric_names_the_available_ones() -> None:
    with pytest.raises(RubricError, match="oscqr"):
        load_rubric("no-such-rubric")


@pytest.mark.parametrize(
    ("damage", "expected_message"),
    [
        (("text_included = true", "text_included = false"), "text_included = false"),
        (('text = "Workspace wording."', ""), "has no text"),
        (('section = "only"', 'section = "other"'), "unknown section"),
        (('id = "A"', ""), "missing 'id'"),
        (("[rubric]", "[rubrik]"), r"missing \[rubric\]"),
        (("[[standard]]", "[[standrad]]"), "no standards"),
    ],
)
def test_malformed_rubric_is_rejected(
    tmp_path: Path, damage: tuple[str, str], expected_message: str
) -> None:
    body = MINIMAL_RUBRIC.format(rubric_id="bad").replace(*damage)
    rubric_path = write_workspace_rubric(tmp_path, "bad", body)
    with pytest.raises(RubricError, match=expected_message):
        load_rubric_file(rubric_path)


def test_duplicate_standard_id_is_rejected(tmp_path: Path) -> None:
    body = MINIMAL_RUBRIC.format(rubric_id="bad")
    body += '\n[[standard]]\nid = "A"\nsection = "only"\ntext = "Again."\n'
    with pytest.raises(RubricError, match="duplicate"):
        load_rubric_file(write_workspace_rubric(tmp_path, "bad", body))


def test_invalid_toml_and_missing_file_raise_rubric_error(tmp_path: Path) -> None:
    broken_path = write_workspace_rubric(tmp_path, "broken", "[rubric\n")
    with pytest.raises(RubricError, match="not valid TOML"):
        load_rubric_file(broken_path)
    with pytest.raises(RubricError):
        load_rubric_file(tmp_path / "absent.toml")


def test_cli_lists_and_shows_rubrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An isolated workspace keeps the CLI from writing a default config.yaml
    # into the repository root, which the secrets gate stage then flags.
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "200")
    runner = CliRunner()

    listing = runner.invoke(app, ["design", "rubrics"])
    assert listing.exit_code == 0
    assert "oscqr" in listing.output and "qm-he-7" in listing.output

    shown = runner.invoke(app, ["design", "rubric"])
    assert shown.exit_code == 0
    assert "Course includes Welcome and Getting Started content." in shown.output

    reference = runner.invoke(app, ["design", "rubric", "qm-he-7"])
    assert reference.exit_code == 0
    assert "held by reference" in reference.output

    unknown = runner.invoke(app, ["design", "rubric", "nope"])
    assert unknown.exit_code == 1
