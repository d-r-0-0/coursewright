"""Schedule files: the tab-separated format Phase 4 reads and writes.

A *schedule file* tells Phase 4 (Dates) when each unit opens and when its
work is due.  Schedules name real terms and real deadlines, so they live in
the instructor's private workspace (``COURSEWRIGHT_WORKSPACE``), never in
this repository.

File format
-----------

Plain text, UTF-8, one unit per line, **tab-separated**::

    # Synthetic Course 101 - Example Term (comments start with "#")
    # unlock_hour: 8
    Unit<TAB>Assigned<TAB>1st Post (@ noon)<TAB>Due (@ noon)
    Unit 01<TAB>09/02/2030<TAB>09/06/2030<TAB>09/13/2030
    Unit 02<TAB>09/02/2030<TAB><TAB>09/13/2030
    Unit 03<TAB>09/09/2030<TAB>09/13/2030<TAB>09/20/2030

* Column 0 is the unit name.  A module is matched to a row when its name
  starts with (or contains) the unit name, so ``Unit 01`` matches the
  module ``Unit 01: Getting Started``.  Use two-digit unit numbers:
  ``Unit 1`` would also match ``Unit 10``.
* Column 1, *Assigned*, is the day the unit unlocks.
* Column 2, *1st Post*, is the day the first discussion post is due.  It is
  **optional**: leave the cell empty for a unit without a discussion (the
  row still needs all three tabs).
* Column 3, *Due*, is the day all of the unit's content is due.
* Dates are ``MM/DD/YYYY`` (``MM/DD/YY`` is also accepted).
* Lines that are empty, start with ``#``, or are the header row (the unit
  cell contains ``Unit`` and the second cell contains ``Assigned``) are
  skipped.  A row with fewer than four columns, or with a date that cannot
  be read, is skipped and reported as a warning; it never aborts the file.
* The optional directive ``# unlock_hour: N`` (0-23) sets the hour units
  unlock.  It applies to the rows *below* it and defaults to ``0``
  (midnight).  First-post and due times are always noon.

Canvas dates
------------

Every date produced here is formatted with the core configuration constant
(``coursewright.core.config.settings.DATE_FORMAT_OUTPUT``,
``YYYY-MM-DDTHH:MM:SS``): naive course-local time with **no ``Z`` suffix**
(AGENTS.md rule 3.10).  Canvas reads a ``Z`` as UTC and shows students a due
date that is hours off.

Library code is silent (rule 3.8): problems found while reading a file are
returned as warning strings for ``ui/`` to show.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from coursewright.core.config import settings

# Optional schedule-file directive, e.g. "# unlock_hour: 8"
UNLOCK_HOUR_PATTERN = re.compile(r"#\s*unlock_hour:\s*(\d{1,2})\s*$")

# Accepted spellings of a schedule date, tried in this order.
SCHEDULE_DATE_FORMATS = ("%m/%d/%y", "%m/%d/%Y")

# Schedule-file display format (and the generator's output format).
SCHEDULE_DISPLAY_FORMAT = "%m/%d/%Y"

FIRST_POST_HOUR = 12
DUE_HOUR = 12


def parse_schedule_date(date_text: str) -> datetime:
    """Read one schedule date (``MM/DD/YY`` or ``MM/DD/YYYY``)."""
    for date_format in SCHEDULE_DATE_FORMATS:
        try:
            return datetime.strptime(date_text, date_format)  # noqa: DTZ007 - naive on purpose
        except ValueError:
            continue
    raise ValueError(f"Could not parse date: {date_text}")


def parse_schedule_text(
    schedule_text: str, warnings: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Parse schedule text into a mapping of unit name to date info.

    Each value holds ``unlock_at``, ``first_post_at`` (``None`` when the
    unit has no discussion) and ``due_at`` as Canvas date strings, plus
    ``unlock_display``, ``first_post_display`` and ``due_display`` as
    ``MM/DD/YYYY`` text for people.

    Args:
        schedule_text: The contents of a schedule file.
        warnings: Optional list that receives one message per skipped row.
    """
    problems = warnings if warnings is not None else []
    canvas_format = settings.DATE_FORMAT_OUTPUT
    schedule: dict[str, dict[str, Any]] = {}
    unlock_hour = 0

    for line_number, row in enumerate(csv.reader(io.StringIO(schedule_text), delimiter="\t"), 1):
        # Skip empty lines and comments (a comment may be a directive).
        if not row or row[0].strip().startswith("#"):
            directive = UNLOCK_HOUR_PATTERN.match(row[0].strip()) if row else None
            if directive:
                unlock_hour = int(directive.group(1))
            continue

        if len(row) < 4:
            problems.append(f"Line {line_number}: Skipping (expected 4 columns, got {len(row)})")
            continue

        # Skip the header row.
        if "Unit" in row[0] and "Assigned" in row[1]:
            continue

        unit_name = row[0].strip()
        first_post_text = row[2].strip()

        try:
            unlock_date = parse_schedule_date(row[1].strip())
            due_date = parse_schedule_date(row[3].strip())
            first_post_date = parse_schedule_date(first_post_text) if first_post_text else None

            # CRITICAL: no "Z" suffix.  Canvas treats these as course-local time.
            schedule[unit_name] = {
                "unlock_at": unlock_date.replace(hour=unlock_hour).strftime(canvas_format),
                "first_post_at": (
                    first_post_date.replace(hour=FIRST_POST_HOUR).strftime(canvas_format)
                    if first_post_date
                    else None
                ),
                "due_at": due_date.replace(hour=DUE_HOUR).strftime(canvas_format),
                "unlock_display": unlock_date.strftime(SCHEDULE_DISPLAY_FORMAT),
                "first_post_display": (
                    first_post_date.strftime(SCHEDULE_DISPLAY_FORMAT) if first_post_date else ""
                ),
                "due_display": due_date.strftime(SCHEDULE_DISPLAY_FORMAT),
            }
        except ValueError as err:
            problems.append(f"Line {line_number}: Could not parse dates: {err}")

    return schedule


def parse_schedule_file(
    schedule_path: Path | str, warnings: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Parse a schedule file; see :func:`parse_schedule_text`.

    Raises:
        FileNotFoundError: when the file does not exist.
    """
    resolved_path = Path(schedule_path)
    if not resolved_path.exists():
        raise FileNotFoundError(f"Schedule file not found: {resolved_path}")
    return parse_schedule_text(resolved_path.read_text(encoding="utf-8"), warnings)


def read_schedule_rows(schedule_text: str) -> list[tuple[str, list[str]]]:
    """Return ``(unit name, [date texts])`` for each non-comment row.

    A looser reader than :func:`parse_schedule_text`: it keeps the raw date
    text so the calendar checker can report a cell it cannot parse.
    """
    rows: list[tuple[str, list[str]]] = []
    for raw_line in schedule_text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        rows.append((fields[0].strip(), [field.strip() for field in fields[1:]]))
    return rows
