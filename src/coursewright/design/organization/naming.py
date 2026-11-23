"""Naming conventions for Phase 2, as configuration rather than constants.

Every institution, publisher and instructor names things differently, so
nothing here is wired into the planning code.  :class:`NamingConvention`
carries the rules (unit-module pattern, item-prefix pattern, letter
orderings, the single assignment group's name, the grading scheme to
enable) and every planner takes one.  The defaults describe a generic
``U##X##`` scheme; pass your own to change any part of it.

Item titles look like ``U03A02: Lab - Subnetting`` with the default
convention: ``U`` + unit number + a letter code + a sequence number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NamingConvention:
    """The rules a course follows for module and item names."""

    # Modules whose items get re-sorted, e.g. "Unit 01 Intro".
    unit_module_pattern: str = r"^Unit \d\d"
    # Item prefix with three groups: unit number, letter code, sequence.
    item_prefix_pattern: str = r"^U(\d\d)([A-Z])(\d\d)"
    # Item prefix template; fields: unit, letter, sequence.
    item_prefix_template: str = "U{unit:02d}{letter}{sequence:02d}"
    # Separator between the descriptor and the item's own text.
    descriptor_separator: str = " — "
    # Order of letter codes inside a module (earlier letters sort first).
    module_item_letter_order: tuple[str, ...] = ("R", "V", "A", "T")
    # Order of letter codes in the gradebook.
    gradebook_letter_order: tuple[str, ...] = ("V", "R", "A", "D", "T")
    # Name of the one assignment group the gradebook pass leaves behind.
    coursework_group_name: str = "Coursework"
    # Title of the grading scheme to enable; None leaves the scheme alone.
    grading_scheme_title: str | None = None
    # Module-name fragments that mark an introductory module (never renamed).
    intro_module_keywords: tuple[str, ...] = (
        "get help",
        "resources",
        "assignments",
        "syllabus",
        "introduction",
        "getting started",
    )
    # Module naming schemes: scheme name -> label placed before the number.
    module_scheme_labels: dict[str, str] = field(
        default_factory=lambda: {"unit": "Unit", "week": "Week", "module": "Module"}
    )
    # Digits a scheme pads its number to (0 = no padding).
    module_scheme_number_width: dict[str, int] = field(
        default_factory=lambda: {"unit": 2, "week": 0, "module": 0}
    )

    @property
    def unit_module_regex(self) -> re.Pattern[str]:
        return re.compile(self.unit_module_pattern)

    @property
    def item_prefix_regex(self) -> re.Pattern[str]:
        return re.compile(self.item_prefix_pattern)

    def parse_item_prefix(self, title: str) -> tuple[int, str, int] | None:
        """Return ``(unit, letter, sequence)`` for a prefixed title, else None."""
        matched = self.item_prefix_regex.match(title)
        if not matched:
            return None
        return int(matched.group(1)), matched.group(2), int(matched.group(3))

    def format_item_prefix(self, unit: int, letter: str, sequence: int) -> str:
        """Build the prefix text for one item, e.g. ``U01R02``."""
        return self.item_prefix_template.format(unit=unit, letter=letter, sequence=sequence)

    def module_item_sort_key(self, title: str) -> tuple[int, int]:
        """Sort key placing items by letter code then sequence.

        Titles without a recognised prefix sort last, keeping their order.
        """
        parsed = self.parse_item_prefix(title)
        if parsed is None:
            return (9, 99)
        _, letter, sequence = parsed
        if letter not in self.module_item_letter_order:
            return (9, 99)
        return (self.module_item_letter_order.index(letter) + 1, sequence)

    def gradebook_sort_key(self, name: str) -> tuple:
        """Sort key: unit, then letter order, then sequence, then name."""
        parsed = self.parse_item_prefix(name)
        if parsed is None:
            return (1, 0, 0, 0, name)
        unit, letter, sequence = parsed
        order = (
            self.gradebook_letter_order.index(letter)
            if letter in self.gradebook_letter_order
            else 9
        )
        return (0, unit, order, sequence, name)


DEFAULT_NAMING = NamingConvention()
