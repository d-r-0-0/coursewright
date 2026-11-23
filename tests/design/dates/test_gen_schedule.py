"""Schedule generation and calendar checks (ported from test_gen_schedule.py).

The calendar is synthetic: a made-up 2030 term with one Monday holiday and a
break week, laid out like a real fall term so the week math is exercised the
same way.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from coursewright.core.config.calendar import AcademicCalendarConfig
from coursewright.design.dates.calendar_check import (
    check_schedule_file,
    check_schedule_text,
    count_conflicts,
)
from coursewright.design.dates.generate import (
    CourseScheduleSpec,
    assign_units_to_weeks,
    build_instructional_weeks,
    build_schedule_rows,
    render_schedule_file,
    write_schedule_file,
)
from coursewright.design.dates.schedule import parse_schedule_text

DISCUSSION_UNITS = (1, 3, 5, 7, 9, 11, 13)

NO_CLASS_DAYS = {date(2030, 9, 2): "Synthetic Holiday"}
for break_day in range(25, 31):
    NO_CLASS_DAYS[date(2030, 11, break_day)] = "Synthetic Break"
NO_CLASS_DAYS[date(2030, 12, 1)] = "Synthetic Break"
CALENDAR = AcademicCalendarConfig(term_name="Synthetic Term 2030", no_class_days=NO_CLASS_DAYS)

FIRST_HALF = CourseScheduleSpec(
    "SYN101", "syn101_first", date(2030, 8, 26), date(2030, 10, 11), 2, 15, "first 7 weeks"
)
SECOND_HALF = CourseScheduleSpec(
    "SYN102", "syn102_second", date(2030, 10, 14), date(2030, 12, 11), 2, 15, "second 7 weeks"
)
FULL_TERM = CourseScheduleSpec(
    "SYN103", "syn103_full", date(2030, 8, 26), date(2030, 12, 11), 1, 14, "full term"
)
ALL_SPECS = (FIRST_HALF, SECOND_HALF, FULL_TERM)


def rows_for(spec: CourseScheduleSpec) -> dict[str, tuple[str, str, str, str]]:
    return {row[0]: row for row in build_schedule_rows(spec, CALENDAR, DISCUSSION_UNITS)}


def test_compressed_session_drops_the_break_week() -> None:
    weeks = build_instructional_weeks(SECOND_HALF, CALENDAR)

    assert date(2030, 11, 25) not in weeks
    assert len(weeks) == 7


def test_full_semester_has_fourteen_instructional_weeks() -> None:
    weeks = build_instructional_weeks(FULL_TERM, CALENDAR)

    assert len(weeks) == 14
    assert date(2030, 11, 25) not in weeks


def test_deadlines_extend_across_the_closure() -> None:
    rows = rows_for(SECOND_HALF)

    assert rows["Unit 11"][3] == "12/06/2030"
    assert rows["Unit 12"][3] == "12/06/2030"


def test_final_week_stacks_remaining_units() -> None:
    rows = rows_for(SECOND_HALF)

    for unit_name in ("Unit 13", "Unit 14", "Unit 15"):
        assert rows[unit_name][1] == "12/02/2030"
        # The Friday after is past the session end, so it is capped there.
        assert rows[unit_name][3] == "12/11/2030"


def test_unlock_shifts_off_a_holiday() -> None:
    rows = rows_for(FIRST_HALF)

    assert rows["Unit 03"][1] == "09/03/2030"
    assert rows["Unit 04"][1] == "09/03/2030"


def test_first_posts_only_on_discussion_units() -> None:
    for row in build_schedule_rows(SECOND_HALF, CALENDAR, DISCUSSION_UNITS):
        unit_number = int(row[0].split()[1])

        assert bool(row[2]) == (unit_number in DISCUSSION_UNITS)


def test_discussion_units_default_to_the_configured_ones() -> None:
    default_rows = build_schedule_rows(FIRST_HALF, CALENDAR)

    assert default_rows == build_schedule_rows(FIRST_HALF, CALENDAR, DISCUSSION_UNITS)


def test_no_generated_date_lands_on_a_no_class_day() -> None:
    for spec in ALL_SPECS:
        for row in build_schedule_rows(spec, CALENDAR, DISCUSSION_UNITS):
            for date_text in row[1:]:
                if not date_text:
                    continue
                month, day, year = (int(part) for part in date_text.split("/"))

                assert not CALENDAR.is_no_class_day(date(year, month, day))


def test_unit_count_is_honoured_for_every_pacing() -> None:
    for spec in ALL_SPECS:
        weeks = build_instructional_weeks(spec, CALENDAR)

        assert len(assign_units_to_weeks(spec, weeks)) == spec.unit_count


def test_session_without_weeks_raises() -> None:
    closed = CourseScheduleSpec(
        "SYN", "syn_closed", date(2030, 11, 25), date(2030, 11, 29), 1, 3, "closed"
    )

    with pytest.raises(ValueError, match="no instructional weeks"):
        build_schedule_rows(closed, CALENDAR, DISCUSSION_UNITS)


def test_rendered_file_round_trips_through_the_parser_and_checker() -> None:
    text = render_schedule_file(SECOND_HALF, CALENDAR, DISCUSSION_UNITS)
    warnings: list[str] = []

    schedule = parse_schedule_text(text, warnings)

    assert warnings == []
    assert len(schedule) == SECOND_HALF.unit_count
    assert "Synthetic Break" in text
    assert "Synthetic Term 2030" in text
    assert schedule["Unit 15"]["due_at"] == "2030-12-11T12:00:00"
    assert count_conflicts(check_schedule_text(text, CALENDAR)) == 0


def test_rendered_file_mentions_assumptions() -> None:
    drafted = CourseScheduleSpec(
        "SYN", "syn_draft", date(2030, 8, 26), date(2030, 12, 11), 1, 3, "draft", "units assumed"
    )

    assert "DRAFT ASSUMPTION: units assumed" in render_schedule_file(drafted, CALENDAR)


def test_checker_flags_closed_days_and_unreadable_dates() -> None:
    text = (
        "# comment\n"
        "Unit 01\t09/02/2030\t\t09/13/2030\n"
        "Unit 02\t09/09/2030\t11/27/2030\tsoon\n"
    )

    findings = check_schedule_text(text, CALENDAR)

    assert [(f.unit_name, f.label, f.is_conflict) for f in findings] == [
        ("Unit 01", "Assigned", True),
        ("Unit 02", "1st Post", True),
        ("Unit 02", "Due", False),
    ]
    assert findings[0].reason == "Synthetic Holiday"
    assert count_conflicts(findings) == 2


def test_check_schedule_file_reads_from_disk(tmp_path: Path) -> None:
    schedule_path = tmp_path / "schedule.txt"
    schedule_path.write_text("Unit 01\t09/09/2030\t\t09/13/2030\n", encoding="utf-8")

    assert check_schedule_file(schedule_path, CALENDAR) == []


def test_write_schedule_file_refuses_to_overwrite(tmp_path: Path) -> None:
    output_path = tmp_path / "schedule.txt"
    text = render_schedule_file(FIRST_HALF, CALENDAR, DISCUSSION_UNITS)

    write_schedule_file(text, output_path)
    with pytest.raises(FileExistsError):
        write_schedule_file(text, output_path)
    write_schedule_file("replaced\n", output_path, overwrite=True)

    assert output_path.read_text(encoding="utf-8") == "replaced\n"
