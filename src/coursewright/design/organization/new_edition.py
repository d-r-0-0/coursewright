"""Sort a publisher's flat content dump into unit modules.

Ported from COURSE_BUILDER ``cantrips/organize_new_edition.py``.  Takes the
flat module an LMS-integration import leaves behind and plans:

* one module per unit (titles come from :class:`NewEditionLayout`);
* each kept item renamed with the naming convention's prefix and ordered
  chapter, read, flashcards, slides, watch, simulations, labs, projects, quiz;
* unwanted publisher extras removed;
* course-level items (setup, orientation) moved to a resources module;
* un-numbered supplementals held back until the caller maps them to a unit,
  because their position in the dump is not reliable.

Which unit titles, resource-module title, removable prefixes and watch-video
mappings apply is the course's business, so they arrive as
:class:`NewEditionLayout`.  Writes go through :class:`SafeCanvasWriter`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteOutcome
from coursewright.design.organization.naming import DEFAULT_NAMING, NamingConvention

# Ordering weight per category (lower = earlier in the module).
CATEGORY_ORDER = {
    "chapter": 10,
    "read": 20,
    "flashcards": 30,
    "powerpoints": 40,
    "watch": 50,
    "sim": 60,
    "vlab": 70,
    "xlab": 75,
    "case": 90,
    "handson": 100,
    "quiz": 110,
}

# Categories numbered in the assignment sequence.
ASSIGNMENT_CATEGORIES = ("sim", "vlab", "xlab", "case", "handson")

# Fixed reading sequence numbers and display labels.
READING_SEQUENCE = {
    "chapter": (1, "Chapter"),
    "read": (2, "Read"),
    "flashcards": (3, "Flashcards"),
    "powerpoints": (4, "PowerPoints"),
}

# Raw-title shapes for "<Label>: Module N <topic>" items -> category.
MODULE_LABELLED_CATEGORIES = (
    ("Read", "read", True),
    ("Flashcards", "flashcards", True),
    ("PowerPoints", "powerpoints", True),
    ("Case Projects", "case", False),
    ("Hands-On Projects", "handson", False),
    ("Quiz", "quiz", False),
)


@dataclass(frozen=True)
class NewEditionLayout:
    """The course-specific target layout (configuration, never constants)."""

    # unit number -> full module title
    unit_titles: dict[int, str] = field(default_factory=dict)
    # Module that receives course-level items.
    resources_title: str = "Start Here: Course Resources"
    # Watch-video topic -> unit number (None = course level).
    watch_unit: dict[str, int | None] = field(default_factory=dict)
    # Raw-title prefixes of publisher extras to delete.
    remove_prefixes: tuple[str, ...] = ()

    def unit_title(self, unit: int) -> str:
        return self.unit_titles.get(unit, f"Unit {unit:02d}")


def classify(title: str, layout: NewEditionLayout) -> dict[str, Any]:
    """Return a classification dict for a raw module-item title."""
    for prefix in layout.remove_prefixes:
        if title.startswith(prefix):
            return {"action": "remove"}

    matched = re.match(r"Chapter 0?(\d+):\s*(.+)", title)
    if matched:
        return {
            "action": "keep", "unit": int(matched.group(1)), "cat": "chapter",
            "sub": 0, "desc": matched.group(2).strip(),
        }  # fmt: skip

    for label, category, keeps_descriptor in MODULE_LABELLED_CATEGORIES:
        matched = re.match(rf"{re.escape(label)}:\s*Module 0?(\d+)\s+(.+)", title)
        if matched:
            return {
                "action": "keep", "unit": int(matched.group(1)), "cat": category,
                "sub": 0, "desc": matched.group(2).strip() if keeps_descriptor else "",
            }  # fmt: skip

    matched = re.match(r"Simulation (\d+)-(\d+):\s*(.+)", title)
    if matched:
        return {
            "action": "keep", "unit": int(matched.group(1)), "cat": "sim",
            "sub": int(matched.group(2)), "n": int(matched.group(1)),
            "x": int(matched.group(2)), "desc": matched.group(3).strip(),
        }  # fmt: skip

    matched = re.match(r"Virtual Lab (\d+)-(\d+),\s*(Guided|Advanced):\s*(.+)", title)
    if matched:
        return {
            "action": "keep", "unit": int(matched.group(1)), "cat": "vlab",
            "sub": int(matched.group(2)), "n": int(matched.group(1)),
            "x": int(matched.group(2)), "level": matched.group(3),
            "desc": matched.group(4).strip(),
        }  # fmt: skip

    # Old deprecated integration -> remove.
    if re.match(r"Live Virtual Machine Lab (\d+)-(\d+):\s*(.+)", title):
        return {"action": "remove", "reason": "live_vm"}

    # Expert / Advanced labs carry no unit number; the caller maps them.
    if re.match(r"(Expert|Advanced) Virtual Lab", title):
        return {"action": "supplemental", "cat": "xlab", "sub": 0, "desc": title.strip()}

    matched = re.match(r"Watch:\s*(.+)", title)
    if matched:
        description = matched.group(1).strip()
        for topic, unit in layout.watch_unit.items():
            if topic in description:
                if unit:
                    return {
                        "action": "keep", "unit": unit, "cat": "watch",
                        "sub": 0, "desc": description,
                    }  # fmt: skip
                return {"action": "course"}
        return {"action": "supplemental", "cat": "watch", "sub": 0, "desc": description}

    return {"action": "course"}


def build_title(
    info: dict[str, Any],
    assignment_index: int,
    video_index: int,
    naming: NamingConvention = DEFAULT_NAMING,
) -> str:
    """Build the prefixed display title for a kept item."""
    unit = info["unit"]
    category = info["cat"]
    separator = naming.descriptor_separator

    def prefix(letter: str, sequence: int) -> str:
        return naming.format_item_prefix(unit, letter, sequence)

    if category in READING_SEQUENCE:
        sequence, label = READING_SEQUENCE[category]
        return f"{prefix('R', sequence)}: {label}{separator}{info['desc']}"
    if category == "watch":
        return f"{prefix('V', video_index)}: Watch{separator}{info['desc']}"
    if category == "quiz":
        return f"{prefix('T', 1)}: Quiz{separator}Module {unit:02d}"
    if category == "sim":
        return (
            f"{prefix('A', assignment_index)}: Simulation "
            f"{info['n']}-{info['x']}{separator}{info['desc']}"
        )
    if category == "vlab":
        return (
            f"{prefix('A', assignment_index)}: Virtual Lab "
            f"{info['n']}-{info['x']} ({info['level']}){separator}{info['desc']}"
        )
    if category == "xlab":
        return f"{prefix('A', assignment_index)}: {info['desc']}"
    if category == "case":
        return f"{prefix('A', assignment_index)}: Case Projects"
    if category == "handson":
        return f"{prefix('A', assignment_index)}: Hands-On Projects"
    return info.get("desc", "")


@dataclass(frozen=True)
class PlannedItem:
    item: dict[str, Any]
    new_title: str
    position: int


@dataclass
class NewEditionPlan:
    course_id: int
    source_module_id: int
    layout: NewEditionLayout
    units: dict[int, list[PlannedItem]] = field(default_factory=dict)
    removes: list[dict[str, Any]] = field(default_factory=list)
    course_items: list[dict[str, Any]] = field(default_factory=list)
    holds: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NewEditionResult:
    plan: NewEditionPlan
    executed: bool
    outcomes: list[WriteOutcome] = field(default_factory=list)
    operations_applied: int = 0
    stopped_at_limit: bool = False


def plan_unit(
    records: list[dict[str, Any]], naming: NamingConvention = DEFAULT_NAMING
) -> list[PlannedItem]:
    """Sort one unit's kept items and assign positions and new titles."""
    ordered = sorted(
        records, key=lambda record: (CATEGORY_ORDER[record["info"]["cat"]], record["info"]["sub"])
    )
    planned = []
    assignment_counter = 0
    video_counter = 1  # V01 is reserved for the instructor lecture.
    for position, record in enumerate(ordered, start=1):
        category = record["info"]["cat"]
        assignment_index = video_index = 0
        if category in ASSIGNMENT_CATEGORIES:
            assignment_counter += 1
            assignment_index = assignment_counter
        elif category == "watch":
            video_counter += 1
            video_index = video_counter
        title = build_title(record["info"], assignment_index, video_index, naming)
        planned.append(PlannedItem(record["item"], title, position))
    return planned


def plan_new_edition(
    client: CanvasClient,
    course_id: int,
    source_module_id: int,
    layout: NewEditionLayout,
    naming: NamingConvention = DEFAULT_NAMING,
) -> NewEditionPlan:
    """Read the source module's items and plan the reorganization (no writes)."""
    items = client.get_module_items(course_id, source_module_id)
    plan = NewEditionPlan(course_id, source_module_id, layout)
    by_unit: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        info = classify(item.get("title", ""), layout)
        record = {"item": item, "info": info}
        if info["action"] == "remove":
            plan.removes.append(item)
        elif info["action"] == "course":
            plan.course_items.append(item)
        elif info["action"] == "supplemental":
            plan.holds.append(item)
        else:
            by_unit.setdefault(info["unit"], []).append(record)
    plan.units = {unit: plan_unit(by_unit[unit], naming) for unit in sorted(by_unit)}
    return plan


def apply_new_edition(
    plan: NewEditionPlan,
    client: CanvasClient,
    writer: SafeCanvasWriter,
    limit: int | None = None,
) -> NewEditionResult:
    """Create modules, move and rename items, delete extras.

    In a dry run nothing is created, so items bound for a module that does
    not exist yet carry ``module_id`` None in the previewed payload.  *limit*
    stops after that many move/rename/delete operations (for a trial run).

    Raises:
        BackupFailedError: a backup could not be saved; nothing later runs.
        WriteFailedError: Canvas refused an operation; later ones are not applied.
    """
    course_id = plan.course_id
    source = plan.source_module_id
    layout = plan.layout
    result = NewEditionResult(plan=plan, executed=writer.execute)
    module_ids = {module["name"]: module["id"] for module in client.get_modules(course_id)}

    def ensure_module(title: str) -> int | None:
        if title in module_ids:
            return module_ids[title]
        outcome = writer.create_module(course_id, {"name": title})
        result.outcomes.append(outcome)
        if outcome.executed:
            module_ids[title] = outcome.result["id"]
            return outcome.result["id"]
        return None

    resources_id = ensure_module(layout.resources_title)
    unit_module_ids = {unit: ensure_module(layout.unit_title(unit)) for unit in plan.units}

    def budget_spent() -> bool:
        if limit and result.operations_applied >= limit:
            result.stopped_at_limit = True
            return True
        return False

    for unit, planned_items in plan.units.items():
        for planned in planned_items:
            if budget_spent():
                return result
            result.outcomes.append(
                writer.update_module_item(
                    course_id,
                    source,
                    planned.item["id"],
                    {
                        "module_id": unit_module_ids[unit],
                        "position": planned.position,
                        "title": planned.new_title,
                    },
                )
            )
            result.operations_applied += 1

    for position, item in enumerate(plan.course_items, start=1):
        if budget_spent():
            return result
        result.outcomes.append(
            writer.move_module_item(course_id, source, item["id"], resources_id, position)
        )
        result.operations_applied += 1

    for item in plan.removes:
        if budget_spent():
            return result
        result.outcomes.append(writer.delete_module_item(course_id, source, item["id"]))
        result.operations_applied += 1
    return result
