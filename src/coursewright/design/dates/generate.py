"""Generate a schedule file that respects the academic calendar.

Ported from the COURSE_BUILDER ``gen_schedule`` cantrip.  Week math done by
hand is where date drift comes from, so the session window and pacing live in
a :class:`CourseScheduleSpec` and the dates are derived from it.

Pattern rules encoded here:

* units unlock on the week's Monday (shifted to the next class day if
  campus is closed);
* discussion units get a first post on that week's Friday;
* content is due the *following* Friday at noon;
* a week whose Friday is a no-class day is a break week: deadlines extend
  past it instead of landing inside it;
* leftover units stack into the final instructional week.

The cantrip's hard-coded term specs and closure dates are gone.  A spec is
plain data supplied by the caller (from the private workspace), and the
no-class days come from an :class:`AcademicCalendarConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from coursewright.core.config import settings
from coursewright.core.config.calendar import AcademicCalendarConfig
from coursewright.design.dates.schedule import SCHEDULE_DISPLAY_FORMAT

MONDAY = 0
FRIDAY = 4


@dataclass(frozen=True)
class CourseScheduleSpec:
    """One course's session window and pacing pattern."""

    course_code: str
    file_stem: str
    session_start: date
    session_end: date
    units_per_week: int
    unit_count: int
    description: str
    assumptions: str = ""


def _week_start(day: date) -> date:
    """Return the Monday of the week containing ``day``."""
    return day - timedelta(days=day.weekday())


def _friday_of(week_start: date) -> date:
    """Return the Friday of a week given its Monday."""
    return week_start + timedelta(days=FRIDAY - MONDAY)


def build_instructional_weeks(
    spec: CourseScheduleSpec, calendar: AcademicCalendarConfig
) -> list[date]:
    """Return week-start Mondays that can actually carry units."""
    weeks: list[date] = []
    current_week = _week_start(spec.session_start)

    while current_week <= spec.session_end:
        week_friday = _friday_of(current_week)

        # A week only holds units when its deadline Friday is a real
        # class day inside the session window.
        if week_friday <= spec.session_end and not calendar.is_no_class_day(week_friday):
            weeks.append(current_week)

        current_week += timedelta(days=7)

    return weeks


def assign_units_to_weeks(
    spec: CourseScheduleSpec, weeks: list[date]
) -> list[tuple[int, date]]:
    """Map each unit number to its instructional week, stacking leftovers."""
    assignments: list[tuple[int, date]] = []
    unit_number = 1

    for week_index, week_start in enumerate(weeks):
        is_final_week = week_index == len(weeks) - 1
        remaining_units = spec.unit_count - unit_number + 1

        take = remaining_units if is_final_week else spec.units_per_week
        for _ in range(min(take, remaining_units)):
            assignments.append((unit_number, week_start))
            unit_number += 1

    return assignments


def _next_open_friday(
    week_start: date, session_end: date, calendar: AcademicCalendarConfig
) -> date:
    """Return the next Friday that is open, capped at the session end."""
    candidate = _friday_of(week_start)

    while calendar.is_no_class_day(candidate):
        candidate += timedelta(days=7)

    return min(candidate, session_end)


def build_schedule_rows(
    spec: CourseScheduleSpec,
    calendar: AcademicCalendarConfig,
    discussion_units: list[int] | tuple[int, ...] | None = None,
) -> list[tuple[str, str, str, str]]:
    """Produce formatted ``(unit, unlock, first_post, due)`` rows for a course.

    ``discussion_units`` defaults to the configured discussion units.

    Raises:
        ValueError: when the session holds no instructional week.
    """
    units_with_discussion = (
        settings.DISCUSSION_UNITS if discussion_units is None else discussion_units
    )
    weeks = build_instructional_weeks(spec, calendar)
    if not weeks:
        raise ValueError(f"{spec.file_stem}: no instructional weeks found")

    rows: list[tuple[str, str, str, str]] = []

    for unit_number, week_start in assign_units_to_weeks(spec, weeks):
        unlock_day = calendar.next_class_day(max(week_start, spec.session_start))

        first_post_text = ""
        if unit_number in units_with_discussion:
            first_post_day = _next_open_friday(week_start, spec.session_end, calendar)
            first_post_text = first_post_day.strftime(SCHEDULE_DISPLAY_FORMAT)

        due_day = _next_open_friday(week_start + timedelta(days=7), spec.session_end, calendar)

        rows.append(
            (
                f"Unit {unit_number:02d}",
                unlock_day.strftime(SCHEDULE_DISPLAY_FORMAT),
                first_post_text,
                due_day.strftime(SCHEDULE_DISPLAY_FORMAT),
            )
        )

    return rows


def render_schedule_file(
    spec: CourseScheduleSpec,
    calendar: AcademicCalendarConfig,
    discussion_units: list[int] | tuple[int, ...] | None = None,
) -> str:
    """Format the complete schedule file text for a course."""
    weeks = build_instructional_weeks(spec, calendar)
    rows = build_schedule_rows(spec, calendar, discussion_units)

    lines = [
        f"# {spec.course_code} Schedule - {calendar.term_name} ({spec.description})",
        f"# Classes begin: {spec.session_start:%A, %B %d, %Y}",
        f"# Classes end: {spec.session_end:%A, %B %d, %Y}",
        "#",
        "# Format: Unit Name<TAB>Assigned<TAB>1st Post (@ noon)<TAB>Due (@ noon)",
        "# PATTERN:",
        f"# - {spec.units_per_week} unit(s) per week across {len(weeks)} instructional weeks",
        "# - Unlock: week start at 12:00 AM",
        "# - Discussion first post: Friday at 12:00 PM, discussion units only",
        "# - Content due: following Friday at 12:00 PM",
        "# - Leftover units stack into the final instructional week",
        "#",
        "# GENERATED by coursewright.design.dates.generate - do not hand-edit week math.",
    ]

    break_windows = calendar.find_break_windows()
    if break_windows:
        lines.append("# Calendar-aware; no-class windows:")
        for window_start, window_end, reason in break_windows:
            span = (
                f"{window_start:%b %d}"
                if window_start == window_end
                else f"{window_start:%b %d} - {window_end:%b %d}"
            )
            lines.append(f"#   {span}: {reason}")

    if spec.assumptions:
        lines += ["#", f"# DRAFT ASSUMPTION: {spec.assumptions}"]

    lines += ["# Re-check with coursewright.design.dates.calendar_check", ""]
    lines += ["\t".join(row).rstrip("\t") if row[2] == "" else "\t".join(row) for row in rows]

    return "\n".join(lines) + "\n"


def write_schedule_file(
    schedule_text: str, output_path: Path, *, overwrite: bool = False
) -> Path:
    """Write generated schedule text, refusing to replace a file unless asked.

    Raises:
        FileExistsError: when the file exists and ``overwrite`` is false.
    """
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path.name} exists; pass overwrite=True to replace it")
    output_path.write_text(schedule_text, encoding="utf-8")
    return output_path
