"""Academic term calendar configuration and schedule validation helpers.

Ported from COURSE_BUILDER ``lib/academic_calendar.py``.  Institution-specific
dates are no longer hardcoded in the repository; ``AcademicCalendarConfig``
provides neutral empty defaults and loads real term boundaries, holidays, and
session windows from configuration or the instructor's private workspace.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from coursewright.core.config.semester import DEFAULT_SEASONS

WORKSPACE_CALENDAR_FILENAMES: tuple[str, ...] = (
    "calendar.toml",
    "calendar.yaml",
    "calendar.yml",
)


class CalendarConfigError(ValueError):
    """Raised when a term calendar file is unreadable or malformed."""


class AcademicCalendarConfig(BaseModel):
    """Configurable term boundaries, milestones, and no-class days."""

    term_name: str = "Academic Term"
    seasons: tuple[str, ...] = DEFAULT_SEASONS
    no_class_days: dict[date, str] = Field(default_factory=dict)
    milestones: dict[date, str] = Field(default_factory=dict)
    sessions: dict[str, tuple[date, date]] = Field(default_factory=dict)

    def is_no_class_day(self, day: date) -> bool:
        """Return True when campus is closed or no classes meet on ``day``."""
        return day in self.no_class_days

    def describe_day(self, day: date) -> str | None:
        """Return the calendar event for ``day``, or None on a regular day."""
        return self.no_class_days.get(day) or self.milestones.get(day)

    def next_class_day(self, day: date) -> date:
        """Return the first day on or after ``day`` when classes meet."""
        candidate = day
        while self.is_no_class_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    def is_within_session(self, day: date, session: tuple[date, date] | str) -> bool:
        """Return True when ``day`` falls within ``session``'s start/end window."""
        if isinstance(session, str):
            if session not in self.sessions:
                raise KeyError(f"unknown session '{session}' in calendar '{self.term_name}'")
            session_start, session_end = self.sessions[session]
        else:
            session_start, session_end = session
        return session_start <= day <= session_end

    def find_break_windows(self) -> list[tuple[date, date, str]]:
        """Group consecutive no-class days into ``(start, end, reason)`` windows."""
        if not self.no_class_days:
            return []

        ordered_days = sorted(self.no_class_days)
        windows: list[tuple[date, date, str]] = []
        window_start = window_end = ordered_days[0]
        for current_day in ordered_days[1:]:
            if current_day - window_end == timedelta(days=1):
                window_end = current_day
                continue
            windows.append((window_start, window_end, self.no_class_days[window_start]))
            window_start = window_end = current_day
        windows.append((window_start, window_end, self.no_class_days[window_start]))
        return windows


def _coerce_calendar(
    calendar: AcademicCalendarConfig | Mapping[date, str] | None,
) -> AcademicCalendarConfig:
    if isinstance(calendar, AcademicCalendarConfig):
        return calendar
    if isinstance(calendar, Mapping):
        return AcademicCalendarConfig(no_class_days=dict(calendar))
    return load_workspace_calendar()


def is_no_class_day(
    day: date, calendar: AcademicCalendarConfig | Mapping[date, str] | None = None
) -> bool:
    """Return True when campus is closed or no classes meet for students."""
    return _coerce_calendar(calendar).is_no_class_day(day)


def describe_day(day: date, calendar: AcademicCalendarConfig | None = None) -> str | None:
    """Return the calendar event for ``day``, or None if it is a normal day."""
    return _coerce_calendar(calendar).describe_day(day)


def next_class_day(
    day: date, calendar: AcademicCalendarConfig | Mapping[date, str] | None = None
) -> date:
    """Return the first day on or after ``day`` that students have class."""
    return _coerce_calendar(calendar).next_class_day(day)


def is_within_session(day: date, session: tuple[date, date]) -> bool:
    """Return True when ``day`` falls inside ``session``'s start/end window."""
    session_start, session_end = session
    return session_start <= day <= session_end


def find_break_windows(
    calendar: AcademicCalendarConfig | Mapping[date, str] | None = None,
) -> list[tuple[date, date, str]]:
    """Group consecutive no-class days into ``(start, end, reason)`` windows."""
    return _coerce_calendar(calendar).find_break_windows()


def load_calendar_file(calendar_path: Path) -> AcademicCalendarConfig:
    """Load an ``AcademicCalendarConfig`` from a TOML or YAML file on disk."""
    try:
        raw_bytes = calendar_path.read_bytes()
    except OSError as err:
        raise CalendarConfigError(f"{calendar_path}: {err.strerror or err}") from err

    try:
        if calendar_path.suffix.lower() == ".toml":
            document = tomllib.loads(raw_bytes.decode("utf-8"))
        else:
            document = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError) as err:
        raise CalendarConfigError(f"{calendar_path}: invalid calendar file: {err}") from err

    if not isinstance(document, dict):
        raise CalendarConfigError(f"{calendar_path}: expected a mapping at top level")
    payload = document.get("calendar", document)
    if not isinstance(payload, dict):
        raise CalendarConfigError(f"{calendar_path}: [calendar] must be a mapping")

    try:
        return AcademicCalendarConfig.model_validate(payload)
    except ValidationError as err:
        raise CalendarConfigError(f"{calendar_path}: {err}") from err


def load_workspace_calendar(
    workspace_directory: Path | None = None,
) -> AcademicCalendarConfig:
    """Load the term calendar from a private workspace, or return neutral defaults."""
    resolved_workspace = workspace_directory
    if resolved_workspace is None:
        env_workspace = os.environ.get("COURSEWRIGHT_WORKSPACE", "").strip()
        if env_workspace:
            resolved_workspace = Path(env_workspace).expanduser()

    if resolved_workspace is not None:
        for filename in WORKSPACE_CALENDAR_FILENAMES:
            candidate = resolved_workspace / filename
            if candidate.is_file():
                return load_calendar_file(candidate)

        config_candidate = resolved_workspace / "config.yaml"
        if config_candidate.is_file():
            try:
                raw_data = yaml.safe_load(config_candidate.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError) as err:
                raise CalendarConfigError(f"{config_candidate}: {err}") from err
            if isinstance(raw_data, dict) and isinstance(raw_data.get("calendar"), dict):
                try:
                    return AcademicCalendarConfig.model_validate(raw_data["calendar"])
                except ValidationError as err:
                    raise CalendarConfigError(f"{config_candidate}: {err}") from err

    return AcademicCalendarConfig()
