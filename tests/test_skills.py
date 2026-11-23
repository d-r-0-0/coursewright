"""Checks that every agent skill in ``skills/`` is honest and hygienic.

A skill is a thin wrapper (AGENTS.md 3.7): it names commands and library
functions and holds no logic.  The danger is drift: a skill that cites a
command or option the CLI no longer has sends an agent down a dead end.
So these tests read every ``SKILL.md`` and check it against the real
Typer command tree and the real library, with nothing mocked away.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from coursewright.ui.cli.main import app

SKILLS_DIRECTORY = Path(__file__).resolve().parent.parent / "skills"
SKILL_FILES = sorted(SKILLS_DIRECTORY.glob("*/SKILL.md"))

# Code is where commands live: fenced blocks and `inline spans`.
FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
INLINE_SPAN = re.compile(r"`([^`\n]+)`")
LIBRARY_REFERENCE = re.compile(r"^coursewright(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")

# Course codes ("ABC 123") and seasonal terms ("Fall 2026") are never
# appropriate in reusable skills.  Source-specific names are represented by
# one-way fingerprints so this public test does not repeat private identities.
FORBIDDEN_PATTERNS = [
    re.compile(r"\b[a-z]{2,4}[ -]?\d{3}\b", re.IGNORECASE),
    re.compile(r"\b(?:fall|spring|summer|winter) \d{2,4}\b", re.IGNORECASE),
    re.compile(r"\b[FSW]\d{2}\b"),
    re.compile(r"hosted\.panopto", re.IGNORECASE),
    re.compile(r"\b(?:college|university) of\b", re.IGNORECASE),
]
FORBIDDEN_NAME_DIGESTS = {
    "33843d5ed74d74a0c386b3e34f03b7f1f9e0471f3861a633a51abd9e04176d2e",  # pragma: allowlist secret
    "3fc5036ad4c524e3eb1730d1701ef9a49b072f861c2a53c724cbe6bf58e57697",  # pragma: allowlist secret
}


def normalized_word_windows(text: str) -> list[str]:
    """Return one- through three-word casefolded phrases from ``text``."""
    words = re.findall(r"\b\w+\b", text.casefold())
    return [
        " ".join(words[start : start + length])
        for start in range(len(words))
        for length in range(1, min(3, len(words) - start) + 1)
    ]


def read_front_matter(skill_file: Path) -> dict:
    """Return the YAML between the opening ``---`` fences."""
    text = skill_file.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{skill_file} has no front matter"
    front_matter_text, _, _ = text[4:].partition("\n---\n")
    return yaml.safe_load(front_matter_text)


def code_snippets(skill_file: Path) -> list[str]:
    """Every fenced line and inline code span in a skill, in file order."""
    text = skill_file.read_text(encoding="utf-8")
    snippets: list[tuple[int, str]] = []
    for block in FENCED_BLOCK.finditer(text):
        snippets += [(block.start(), line) for line in block.group(1).splitlines()]
    prose = FENCED_BLOCK.sub(lambda match: " " * len(match.group(0)), text)
    snippets += [(span.start(), span.group(1)) for span in INLINE_SPAN.finditer(prose)]
    return [snippet.strip() for _, snippet in sorted(snippets)]


def cli_invocations(skill_file: Path) -> list[list[str]]:
    """Each ``coursewright ...`` command line, split into words."""
    return [
        snippet.split()[1:]
        for snippet in code_snippets(skill_file)
        if snippet.startswith("coursewright ")
    ]


def resolve_command(words: list[str]):
    """Walk the real command tree; return (path, command, leftover words)."""
    command = typer.main.get_command(app)
    path: list[str] = []
    remaining = list(words)
    while remaining and getattr(command, "commands", None):
        if remaining[0] not in command.commands:
            break
        path.append(remaining.pop(0))
        command = command.commands[path[-1]]
    return path, command, remaining


def option_names(command) -> set[str]:
    """Every ``--long``/``-s`` spelling a command accepts."""
    names: set[str] = set()
    for parameter in command.params:
        names.update(getattr(parameter, "opts", []))
        names.update(getattr(parameter, "secondary_opts", []))
    return names


def test_there_is_a_skill_for_each_named_capability() -> None:
    expected = {
        "discussion-review",
        "daily-digest",
        "grade-import",
        "honor-credit",
        "zero-ungraded",
        "weekly-grading",
        "attendance-check",
        "course-diagnose",
        "apply-dates",
        "publish-content",
        "rubric-audit",
        "five-phase-build",
        "unit-pipeline",
    }
    assert expected <= {skill_file.parent.name for skill_file in SKILL_FILES}


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda path: path.parent.name)
def test_front_matter_has_name_and_description(skill_file: Path) -> None:
    front_matter = read_front_matter(skill_file)
    assert front_matter["name"] == skill_file.parent.name
    assert len(front_matter["description"].split()) >= 10


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda path: path.parent.name)
def test_every_cited_command_and_option_exists(skill_file: Path) -> None:
    invocations = cli_invocations(skill_file)
    assert invocations, f"{skill_file} cites no coursewright command"
    runner = CliRunner()
    for words in invocations:
        path, command, remaining = resolve_command(words)
        assert path, f"unknown command: coursewright {' '.join(words)}"
        subcommands = getattr(command, "commands", None)
        if subcommands and remaining and not remaining[0].startswith("-"):
            pytest.fail(f"no subcommand {remaining[0]!r} under {' '.join(path)}")
        unknown = {word for word in remaining if word.startswith("-")} - option_names(command)
        assert not unknown, f"coursewright {' '.join(path)} has no {sorted(unknown)}"
        result = runner.invoke(app, [*path, "--help"])
        assert result.exit_code == 0, f"coursewright {' '.join(path)} --help failed"


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda path: path.parent.name)
def test_every_cited_library_name_imports(skill_file: Path) -> None:
    for snippet in code_snippets(skill_file):
        if not LIBRARY_REFERENCE.match(snippet):
            continue
        parts = snippet.split(".")
        for split_at in range(len(parts), 0, -1):
            try:
                target = importlib.import_module(".".join(parts[:split_at]))
            except ModuleNotFoundError:
                continue
            for attribute in parts[split_at:]:
                target = getattr(target, attribute)
            break
        else:
            pytest.fail(f"{skill_file.parent.name}: cannot import {snippet}")


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda path: path.parent.name)
def test_no_course_term_or_institution_is_named(skill_file: Path) -> None:
    text = skill_file.read_text(encoding="utf-8")
    for pattern in FORBIDDEN_PATTERNS:
        match = pattern.search(text)
        assert match is None, f"{skill_file.parent.name} names {match.group(0)!r}"
    for phrase in normalized_word_windows(text):
        phrase_digest = hashlib.sha256(phrase.encode()).hexdigest()
        assert phrase_digest not in FORBIDDEN_NAME_DIGESTS, (
            f"{skill_file.parent.name} names a source-specific institution"
        )


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda path: path.parent.name)
def test_write_capable_skills_preview_before_execute(skill_file: Path) -> None:
    invocations = cli_invocations(skill_file)
    writes = [words for words in invocations if "--execute" in words]
    if not writes:
        return
    text = skill_file.read_text(encoding="utf-8").lower()
    assert "preview, then execute" in text
    for position, words in enumerate(invocations):
        if "--execute" not in words:
            continue
        preview = [word for word in words if word != "--execute"]
        assert preview in invocations[:position], (
            f"{skill_file.parent.name}: show `coursewright {' '.join(preview)}` "
            "as a dry run before adding --execute"
        )


def test_the_checks_catch_a_broken_skill(tmp_path: Path) -> None:
    """The command check must fail on a command or option that is not real."""
    broken = tmp_path / "broken" / "SKILL.md"
    broken.parent.mkdir()
    broken.write_text(
        "---\nname: broken\ndescription: x\n---\n"
        "`coursewright design dates apply 1 f --force`\n"
        "`coursewright design teleport 1`\n",
        encoding="utf-8",
    )
    first, second = cli_invocations(broken)
    path, command, remaining = resolve_command(first)
    assert path == ["design", "dates", "apply"]
    assert "--force" not in option_names(command)
    path, command, remaining = resolve_command(second)
    assert remaining[0] == "teleport" and getattr(command, "commands", None)
