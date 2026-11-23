"""Normalize item order inside every unit module by prefix.

Ported from COURSE_BUILDER ``cantrips/normalize_units.py``.  Sorts each
unit's items by their letter code and sequence so Canvas shows them in the
intended order (with the default convention: reading, video, assignment,
test).  Items without a recognised prefix stay at the end, in place.

Planning only reads from Canvas.  Applying goes through
:class:`SafeCanvasWriter`, so a dry run makes no write, an execute run saves
a backup of each item before changing it, and the first refusal stops the
run by raising (nothing after it is attempted).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteOutcome
from coursewright.design.organization.naming import DEFAULT_NAMING, NamingConvention


class NoModulesFoundError(Exception):
    """The course returned no modules (wrong course id or token)."""


@dataclass(frozen=True)
class ItemMove:
    """One module item that must change position."""

    module_id: int
    module_name: str
    item_id: int
    title: str
    old_position: int | None
    new_position: int


@dataclass
class UnitOrderPlan:
    """Every reposition needed, plus the module snapshot it was planned from."""

    course_id: int
    moves: list[ItemMove] = field(default_factory=list)
    snapshot: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class UnitOrderResult:
    """What applying a plan did (or, in a dry run, would do)."""

    plan: UnitOrderPlan
    executed: bool
    outcomes: list[WriteOutcome] = field(default_factory=list)


def plan_unit_order(
    client: CanvasClient,
    course_id: int,
    naming: NamingConvention = DEFAULT_NAMING,
) -> UnitOrderPlan:
    """Read the unit modules and list the repositions they need.

    Only modules matching ``naming.unit_module_pattern`` are fetched.

    Raises:
        NoModulesFoundError: when the course has no modules at all.
    """
    modules = client.get_modules(course_id)
    if not isinstance(modules, list) or not modules:
        raise NoModulesFoundError(
            f"No modules found in course {course_id} (check the course id and token)."
        )

    plan = UnitOrderPlan(course_id=course_id)
    unit_pattern = naming.unit_module_regex
    for module in modules:
        if not unit_pattern.match(module["name"]):
            continue
        items = client.get_module_items(course_id, module["id"])
        plan.snapshot.append({"module": module, "items": items})
        ordered = sorted(items, key=lambda item: naming.module_item_sort_key(item.get("title", "")))
        for position, item in enumerate(ordered, start=1):
            if item.get("position") != position:
                plan.moves.append(
                    ItemMove(
                        module_id=module["id"],
                        module_name=module["name"],
                        item_id=item["id"],
                        title=item.get("title", ""),
                        old_position=item.get("position"),
                        new_position=position,
                    )
                )
    return plan


def apply_unit_order(plan: UnitOrderPlan, writer: SafeCanvasWriter) -> UnitOrderResult:
    """Apply (or, on a dry-run writer, preview) every planned reposition.

    Raises:
        BackupFailedError: a backup could not be saved; nothing later runs.
        WriteFailedError: Canvas refused a move; remaining moves are not applied.
    """
    outcomes = [
        writer.update_module_item(
            plan.course_id,
            move.module_id,
            move.item_id,
            {"position": move.new_position},
        )
        for move in plan.moves
    ]
    return UnitOrderResult(plan=plan, executed=writer.execute, outcomes=outcomes)


def normalize_unit_order(
    client: CanvasClient,
    writer: SafeCanvasWriter,
    course_id: int,
    naming: NamingConvention = DEFAULT_NAMING,
) -> UnitOrderResult:
    """Plan and apply in one call; the writer's ``execute`` flag decides."""
    return apply_unit_order(plan_unit_order(client, course_id, naming), writer)
