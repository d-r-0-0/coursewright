"""Check a schedule against the academic calendar.

Ported from the COURSE_BUILDER ``check_calendar`` cantrip.  No deadline or
discussion post should land on a closed-campus day; this catches the class
of error that stays invisible until students complain.  It returns data and
prints nothing (rule 3.8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from coursewright.core.config.calendar import AcademicCalendarConfig
from coursewright.design.dates.schedule import SCHEDULE_DISPLAY_FORMAT, read_schedule_rows

COLUMN_LABELS = ["Assigned", "1st Post", "Due"]


@dataclass(frozen=True)
class CalendarFinding:
    """One problem in a schedule row."""

    unit_name: str
    label: str
    date_text: str
    reason: str
    is_conflict: bool  # False for an unreadable date (a warning, not a conflict)


def check_schedule_text(
    schedule_text: str, calendar: AcademicCalendarConfig
) -> list[CalendarFinding]:
    """Return every date that is unreadable or falls on a no-class day."""
    findings: list[CalendarFinding] = []

    for unit_name, date_fields in read_schedule_rows(schedule_text):
        for column_index, date_text in enumerate(date_fields):
            if not date_text:
                continue
            label = COLUMN_LABELS[column_index] if column_index < len(COLUMN_LABELS) else "Date"

            try:
                parsed_day = datetime.strptime(date_text, SCHEDULE_DISPLAY_FORMAT).date()  # noqa: DTZ007
            except ValueError:
                findings.append(
                    CalendarFinding(unit_name, label, date_text, "unparsable date", False)
                )
                continue

            if calendar.is_no_class_day(parsed_day):
                reason = calendar.describe_day(parsed_day) or "no-class day"
                findings.append(CalendarFinding(unit_name, label, date_text, reason, True))

    return findings


def check_schedule_file(
    schedule_path: Path, calendar: AcademicCalendarConfig
) -> list[CalendarFinding]:
    """Check one schedule file; see :func:`check_schedule_text`."""
    return check_schedule_text(Path(schedule_path).read_text(encoding="utf-8"), calendar)


def count_conflicts(findings: list[CalendarFinding]) -> int:
    """Count findings that are real calendar conflicts (not unreadable dates)."""
    return sum(1 for finding in findings if finding.is_conflict)
