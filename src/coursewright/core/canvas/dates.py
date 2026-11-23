"""Canvas date strings: naive local time, never a ``Z`` or an offset.

AGENTS.md rule 3.10 and COURSE_BUILDER ADR-0101: Canvas reads an ISO date
ending in ``Z`` as UTC and converts it to the course time zone, so a due date
meant as 11:59 PM shows students 7:59 PM.  Every date sent to Canvas is the
plain ``YYYY-MM-DDTHH:MM:SS`` shape, which Canvas treats as course-local time.

This module is the one sanctioned formatter and the one validator the safe
writer applies to every outgoing payload.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# The single sanctioned shape (mirrors COURSE_BUILDER ``DATE_FORMAT_OUTPUT``).
CANVAS_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

_CANVAS_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

# Payload keys Canvas reads as dates.  ``None`` is allowed: it clears the date.
CANVAS_DATE_FIELD_NAMES = frozenset(
    {"due_at", "unlock_at", "lock_at", "delayed_post_at", "start_at", "end_at"}
)


def format_canvas_datetime(moment: datetime) -> str:
    """Format a *naive* datetime the way Canvas wants it.

    Raises:
        ValueError: if *moment* carries a time zone.  Converting is left to
            the caller on purpose, because the right local zone is a property
            of the Canvas course, not of this library.
    """
    if moment.tzinfo is not None:
        raise ValueError(
            "Canvas dates are naive course-local times; convert the datetime "
            "to the course's local time and drop tzinfo before formatting."
        )
    return moment.strftime(CANVAS_DATE_FORMAT)


def validate_canvas_date_fields(payload: Any) -> None:
    """Reject any date in *payload* that Canvas would misread.

    Walks nested dictionaries and lists.  Accepts ``None`` and strings of the
    exact ``YYYY-MM-DDTHH:MM:SS`` shape.

    Raises:
        ValueError: for a ``Z`` suffix, an offset, a ``datetime`` object, or
            any other shape, naming the offending field.
    """
    if isinstance(payload, dict):
        for field_name, value in payload.items():
            if field_name in CANVAS_DATE_FIELD_NAMES:
                _validate_one_date_value(field_name, value)
            else:
                validate_canvas_date_fields(value)
    elif isinstance(payload, list | tuple):
        for element in payload:
            validate_canvas_date_fields(element)


def _validate_one_date_value(field_name: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, datetime):
        raise ValueError(
            f"{field_name}: pass a string from format_canvas_datetime(), not a datetime."
        )
    if not isinstance(value, str) or not _CANVAS_DATE_PATTERN.match(value):
        raise ValueError(
            f"{field_name}={value!r} is not YYYY-MM-DDTHH:MM:SS; "
            "Canvas dates carry no 'Z' suffix and no offset."
        )
