"""The bundled prompt files: packaging, loading and hygiene."""

from __future__ import annotations

import re
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest

from coursewright.core.llm.prompt_library import list_prompt_names, load_prompt

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_PROMPT_NAMES = [
    "ASSIGNMENT_MAP_SYSTEM.md",
    "ASSIGNMENT_MAP_USER.md",
    "DISCUSSION_GRADERATOR.md",
    "FEEDBACK_BATCH.md",
    "FEEDBACK_BATCH_AI.md",
    "FEEDBACK_SYSTEM.md",
    "FEEDBACK_USER.md",
    "FEEDBACK_USER_AI.md",
    "STUDENT_MAP_SYSTEM.md",
    "STUDENT_MAP_USER.md",
]

EMAIL_ADDRESS = re.compile(r"[\w.+-]+@([\w-]+\.)+[\w-]+")


def test_every_ported_prompt_is_bundled() -> None:
    assert list_prompt_names() == EXPECTED_PROMPT_NAMES


@pytest.mark.parametrize("prompt_file_name", EXPECTED_PROMPT_NAMES)
def test_prompt_loads_through_importlib_resources(prompt_file_name: str) -> None:
    resource_text = (
        files("coursewright.core.llm")
        .joinpath("prompts", prompt_file_name)
        .read_text(encoding="utf-8")
    )

    assert load_prompt(prompt_file_name) == resource_text
    assert resource_text.strip()


def test_user_prompt_templates_keep_their_placeholders() -> None:
    """The templates are filled with ``str.format`` by their callers."""
    student_map_prompt = load_prompt("STUDENT_MAP_USER.md").format(
        netacad_students="NETACAD ROWS", canvas_roster="CANVAS ROWS"
    )
    assignment_map_prompt = load_prompt("ASSIGNMENT_MAP_USER.md").format(
        netacad_columns="NETACAD COLUMNS", canvas_assignments="CANVAS ASSIGNMENTS"
    )

    assert "NETACAD ROWS" in student_map_prompt
    assert "CANVAS ROWS" in student_map_prompt
    assert "NETACAD COLUMNS" in assignment_map_prompt
    assert "CANVAS ASSIGNMENTS" in assignment_map_prompt


@pytest.mark.parametrize(
    "prompt_file_name",
    ["MISSING.md", "../model_pricing.json", "prompts/FEEDBACK_SYSTEM.md", ""],
)
def test_unknown_or_path_like_names_are_refused(prompt_file_name: str) -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt(prompt_file_name)


def test_prompts_and_pricing_are_declared_as_package_data() -> None:
    """Without this entry a wheel ships the modules but not the files."""
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert package_data["coursewright.core.llm"] == ["model_pricing.json", "prompts/*.md"]


@pytest.mark.parametrize("prompt_file_name", EXPECTED_PROMPT_NAMES)
def test_prompt_examples_use_only_synthetic_addresses(prompt_file_name: str) -> None:
    """Hygiene: every example address is at example.edu (AGENTS.md 3.13)."""
    for address in EMAIL_ADDRESS.finditer(load_prompt(prompt_file_name)):
        assert address.group(0).endswith("example.edu"), address.group(0)
