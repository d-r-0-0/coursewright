"""Tests for the configurable academic calendar (ported from COURSE_BUILDER)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from coursewright.core.config.calendar import (
    AcademicCalendarConfig,
    CalendarConfigError,
    describe_day,
    find_break_windows,
    is_no_class_day,
    is_within_session,
    load_calendar_file,
    load_workspace_calendar,
    next_class_day,
)

SECOND_SEVEN_WEEKS_SESSION = (date(2026, 10, 13), date(2026, 12, 9))


@pytest.fixture
def sample_fall_calendar() -> AcademicCalendarConfig:
    """Synthetic Fall term calendar matching the ported schedule rules."""
    return AcademicCalendarConfig(
        term_name="Sample Fall 2026",
        no_class_days={
            date(2026, 9, 7): "Holiday (Campus Closed)",
            date(2026, 11, 24): "Faculty Preparation (no classes for students)",
            date(2026, 11, 25): "Holiday (Campus Closed)",
            date(2026, 11, 26): "Holiday (Campus Closed)",
            date(2026, 11, 27): "Holiday (Campus Closed)",
            date(2026, 11, 28): "Holiday (Campus Closed)",
            date(2026, 11, 29): "Holiday (Campus Closed)",
        },
        milestones={
            date(2026, 8, 24): "Day and Night Classes Begin",
            date(2026, 10, 12): "End of the first 7 Weeks",
            date(2026, 10, 13): "Classes Begin for Second 7 Weeks",
            date(2026, 12, 9): "Day Exams & Wednesday Evening Exams",
        },
        sessions={
            "full_semester": (date(2026, 8, 24), date(2026, 12, 9)),
            "first_seven_weeks": (date(2026, 8, 24), date(2026, 10, 12)),
            "second_seven_weeks": SECOND_SEVEN_WEEKS_SESSION,
        },
    )


def test_default_calendar_is_neutral_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    default_calendar = AcademicCalendarConfig()
    assert default_calendar.no_class_days == {}
    assert default_calendar.milestones == {}
    assert default_calendar.sessions == {}
    assert not is_no_class_day(date(2026, 9, 7))
    assert describe_day(date(2026, 9, 7)) is None
    assert find_break_windows() == []


def test_labor_day_is_a_no_class_day(sample_fall_calendar: AcademicCalendarConfig) -> None:
    assert is_no_class_day(date(2026, 9, 7), sample_fall_calendar)
    assert describe_day(date(2026, 9, 7), sample_fall_calendar) == "Holiday (Campus Closed)"


def test_thanksgiving_week_is_fully_closed(sample_fall_calendar: AcademicCalendarConfig) -> None:
    closed_days = [date(2026, 11, day) for day in range(24, 30)]
    assert all(is_no_class_day(day, sample_fall_calendar) for day in closed_days)


def test_regular_class_day_is_not_flagged(sample_fall_calendar: AcademicCalendarConfig) -> None:
    assert not is_no_class_day(date(2026, 11, 23), sample_fall_calendar)
    assert not is_no_class_day(date(2026, 12, 4), sample_fall_calendar)


def test_next_class_day_skips_the_thanksgiving_closure(
    sample_fall_calendar: AcademicCalendarConfig,
) -> None:
    assert next_class_day(date(2026, 11, 24), sample_fall_calendar) == date(2026, 11, 30)


def test_next_class_day_returns_same_day_when_open(
    sample_fall_calendar: AcademicCalendarConfig,
) -> None:
    assert next_class_day(date(2026, 10, 13), sample_fall_calendar) == date(2026, 10, 13)


def test_second_seven_weeks_session_bounds(sample_fall_calendar: AcademicCalendarConfig) -> None:
    assert is_within_session(date(2026, 10, 13), SECOND_SEVEN_WEEKS_SESSION)
    assert is_within_session(date(2026, 12, 9), SECOND_SEVEN_WEEKS_SESSION)
    assert not is_within_session(date(2026, 10, 12), SECOND_SEVEN_WEEKS_SESSION)
    assert sample_fall_calendar.is_within_session(date(2026, 10, 13), "second_seven_weeks")
    with pytest.raises(KeyError, match="unknown session"):
        sample_fall_calendar.is_within_session(date(2026, 10, 13), "nonexistent")


def test_break_windows_group_consecutive_days(sample_fall_calendar: AcademicCalendarConfig) -> None:
    windows = find_break_windows(sample_fall_calendar)
    assert (date(2026, 9, 7), date(2026, 9, 7)) == windows[0][:2]
    assert (date(2026, 11, 24), date(2026, 11, 29)) == windows[1][:2]


def test_mapping_input_is_accepted_directly() -> None:
    raw_holidays = {date(2026, 9, 7): "Closed"}
    assert is_no_class_day(date(2026, 9, 7), raw_holidays)
    assert next_class_day(date(2026, 9, 7), raw_holidays) == date(2026, 9, 8)


def test_workspace_calendar_loads_from_toml_and_config_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calendar_toml = tmp_path / "calendar.toml"
    calendar_toml.write_text(
        '[calendar]\nterm_name = "Workspace Term"\nseasons = ["Spring", "Summer", "Fall"]\n'
        '[calendar.no_class_days]\n"2026-09-07" = "Labor Day"\n'
        '[calendar.milestones]\n"2026-08-24" = "Classes Begin"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
    loaded = load_workspace_calendar()
    assert loaded.term_name == "Workspace Term"
    assert loaded.seasons == ("Spring", "Summer", "Fall")
    assert is_no_class_day(date(2026, 9, 7))
    assert describe_day(date(2026, 8, 24)) == "Classes Begin"

    calendar_toml.unlink()
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        'calendar:\n  term_name: "YAML Term"\n  no_class_days:\n    "2026-11-26": "Autumn Break"\n',
        encoding="utf-8",
    )
    loaded_yaml = load_workspace_calendar(tmp_path)
    assert loaded_yaml.term_name == "YAML Term"
    assert loaded_yaml.is_no_class_day(date(2026, 11, 26))


def test_invalid_calendar_file_raises_calendar_config_error(tmp_path: Path) -> None:
    with pytest.raises(CalendarConfigError):
        load_calendar_file(tmp_path / "missing.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("[calendar\n", encoding="utf-8")
    with pytest.raises(CalendarConfigError, match="invalid calendar file"):
        load_calendar_file(broken)
    bad_types = tmp_path / "bad.yaml"
    bad_types.write_text("calendar:\n  no_class_days:\n    not-a-date: Closed\n", encoding="utf-8")
    with pytest.raises(CalendarConfigError):
        load_calendar_file(bad_types)
