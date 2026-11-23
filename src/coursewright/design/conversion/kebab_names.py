"""Kebab-case file and folder names, with a dry-run-first rename."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

BRACKET_TAG = re.compile(r"\[(.*?)\]")
ZERO_WIDTH = re.compile("[​‌‍]")
SEPARATORS = re.compile(r"[_\s]+")
DISALLOWED = re.compile(r"[^a-zA-Z0-9\-.]")
HYPHEN_RUN = re.compile(r"-+")


def to_kebab_case(name: str, extension: str = "") -> str:
    """Normalise one name; the extension is preserved untouched."""
    stem = name[: len(name) - len(extension)] if extension else name
    stem = BRACKET_TAG.sub(r"\1", stem).strip()
    stem = ZERO_WIDTH.sub("", stem)
    stem = SEPARATORS.sub("-", stem)
    stem = DISALLOWED.sub("", stem).lower()
    stem = HYPHEN_RUN.sub("-", stem).strip("-")
    return stem + extension


@dataclass(frozen=True)
class RenameOutcome:
    old_path: Path
    new_path: Path
    status: str  # "planned", "renamed", "exists" or "error"
    detail: str = ""


def rename_to_kebab_case(root: Path, *, execute: bool = False) -> list[RenameOutcome]:
    """Rename everything under `root`; a dry run unless `execute`.

    Deepest paths go first so files are renamed before their folders.
    An existing target is never overwritten.
    """
    items = sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    outcomes: list[RenameOutcome] = []
    for item in items:
        new_name = to_kebab_case(item.name, item.suffix)
        if new_name == item.name or not new_name:
            continue
        new_path = item.with_name(new_name)
        if not execute:
            outcomes.append(RenameOutcome(item, new_path, "planned"))
        elif new_path.exists():
            outcomes.append(RenameOutcome(item, new_path, "exists", "target exists"))
        else:
            try:
                item.rename(new_path)
            except OSError as err:
                outcomes.append(RenameOutcome(item, new_path, "error", str(err)))
            else:
                outcomes.append(RenameOutcome(item, new_path, "renamed"))
    return outcomes
