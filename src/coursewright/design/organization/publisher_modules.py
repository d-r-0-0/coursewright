"""Rename publisher content modules into one consistent structure.

Ported from COURSE_BUILDER ``scripts/organization/organize_publisher_content.py``.
The prompts, course search and printing of the script belong to ``ui/``;
what remains is the logic: detect a publisher's module-name pattern, plan
the renames, and apply them through :class:`SafeCanvasWriter`.

The script wrote its own whole-course backup; the writer now backs up each
module immediately before changing it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteOutcome
from coursewright.design.organization.naming import DEFAULT_NAMING, NamingConvention

# Publisher module-name shapes.  The "numbered_*" names are generic; the
# first is the dotted "1.1 Topic" style many course publishers use.
PUBLISHER_PATTERNS = {
    "dotted_numbering": r"^\d+\.\d+\s+",
    "numbered_week": r"^Week\s+\d+",
    "numbered_module": r"^Module\s+\d+",
    "numbered_unit": r"^Unit\s+\d+",
}


@dataclass(frozen=True)
class ModuleRename:
    """One module's planned name and position."""

    module_id: int
    old_name: str
    new_name: str
    position: int
    renamed: bool


@dataclass
class ModuleRenamePlan:
    course_id: int
    scheme: str
    entries: list[ModuleRename] = field(default_factory=list)
    skipped_count: int = 0

    @property
    def renames(self) -> list[ModuleRename]:
        return [entry for entry in self.entries if entry.renamed]


@dataclass
class ModuleRenameResult:
    plan: ModuleRenamePlan
    executed: bool
    outcomes: list[WriteOutcome] = field(default_factory=list)


def detect_module_pattern(
    modules: list[dict[str, Any]],
) -> tuple[str | None, dict[str, int]]:
    """Return the publisher pattern matching most modules, and all counts."""
    detected: dict[str, int] = {}
    for pattern_name, pattern_regex in PUBLISHER_PATTERNS.items():
        count = sum(
            1 for module in modules if re.match(pattern_regex, module["name"], re.IGNORECASE)
        )
        if count:
            detected[pattern_name] = count
    if not detected:
        return None, {}
    best_pattern = max(detected.items(), key=lambda pair: pair[1])
    return best_pattern[0], detected


def generate_new_module_name(
    old_name: str,
    naming_scheme: str = "unit",
    unit_number: int | None = None,
    naming: NamingConvention = DEFAULT_NAMING,
) -> str:
    """Build a module name from *old_name* under *naming_scheme*.

    The number is taken from the old name when *unit_number* is None (1 if
    the name has no digits).  An unknown scheme returns *old_name* unchanged.
    """
    if unit_number is None:
        number_match = re.search(r"(\d+)", old_name)
        unit_number = int(number_match.group(1)) if number_match else 1

    topic = re.sub(r"^\d+\.?\d*\s*[-:]?\s*", "", old_name)
    topic = re.sub(r"^(Week|Module|Unit)\s+\d+\s*[-:]?\s*", "", topic, flags=re.IGNORECASE)
    topic = topic.strip()

    label = naming.module_scheme_labels.get(naming_scheme)
    if label is None:
        return old_name
    width = naming.module_scheme_number_width.get(naming_scheme, 0)
    heading = f"{label} {unit_number:0{width}d}"
    return f"{heading} - {topic}" if topic else heading


def plan_module_renames(
    client: CanvasClient,
    course_id: int,
    naming_scheme: str = "unit",
    modules_to_rename: list[str] | None = None,
    naming: NamingConvention = DEFAULT_NAMING,
) -> ModuleRenamePlan:
    """Read the course's modules and plan renames (no writes).

    Introductory modules (``naming.intro_module_keywords``) keep their name.
    When *modules_to_rename* is given, other modules are left alone and
    counted in ``skipped_count``.
    """
    modules = client.get_modules(course_id)
    plan = ModuleRenamePlan(course_id=course_id, scheme=naming_scheme)

    for position, module in enumerate(modules, start=1):
        module_name = module["name"]
        unchanged = ModuleRename(module["id"], module_name, module_name, position, False)

        lowered = module_name.lower()
        if any(keyword in lowered for keyword in naming.intro_module_keywords):
            plan.entries.append(unchanged)
            continue
        if modules_to_rename and module_name not in modules_to_rename:
            plan.skipped_count += 1
            plan.entries.append(unchanged)
            continue

        # Unit numbers skip over modules left unchanged so far.
        unit_number = position - sum(1 for entry in plan.entries if not entry.renamed)
        new_name = generate_new_module_name(module_name, naming_scheme, unit_number, naming)
        plan.entries.append(
            ModuleRename(module["id"], module_name, new_name, position, new_name != module_name)
        )
    return plan


def apply_module_renames(plan: ModuleRenamePlan, writer: SafeCanvasWriter) -> ModuleRenameResult:
    """Apply (or preview, on a dry-run writer) every planned rename.

    Raises:
        BackupFailedError: a backup could not be saved; nothing later runs.
        WriteFailedError: Canvas refused a rename; later renames are not applied.
    """
    outcomes = [
        writer.update_module(
            plan.course_id,
            entry.module_id,
            {"name": entry.new_name, "position": entry.position},
        )
        for entry in plan.renames
    ]
    return ModuleRenameResult(plan=plan, executed=writer.execute, outcomes=outcomes)
