"""Academic semester detection and Canvas term-name matching helpers.

Ported from discussorator ``cli/semester.py`` into ``core/config`` with
institution-neutral defaults and configurable season names.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

# Default three-term academic year order (Jan-Apr, May-Aug, Sep-Dec).
# Institutions with different naming (for example Spring/Summer/Fall)
# can override this in AcademicCalendarConfig or pass `seasons`.
DEFAULT_SEASONS: tuple[str, str, str] = ("Winter", "Summer", "Fall")
SEASONS: tuple[str, str, str] = DEFAULT_SEASONS


def detect_current_semester(
    today: date | None = None,
    seasons: Sequence[str] = DEFAULT_SEASONS,
) -> str:
    """Return the current academic semester as ``"YYYY Season"``."""
    if len(seasons) != 3:
        raise ValueError("seasons must contain exactly three term names")

    reference_date = date.today() if today is None else today
    year = reference_date.year
    month = reference_date.month

    if 1 <= month <= 4:
        return f"{year} {seasons[0]}"
    if 5 <= month <= 8:
        return f"{year} {seasons[1]}"
    return f"{year} {seasons[2]}"


def is_current_semester(
    term_name: str | None,
    current_semester: str,
    *,
    today: date | None = None,
) -> bool:
    """Check whether ``term_name`` matches ``current_semester``.

    Supports full term strings (``"2026 Winter"``, ``"2026 Winter Semester"``)
    and abbreviated term codes returned by Canvas LMS (``"W26"``, ``"SP26"``,
    ``"F25"``, ``"SU25"``).
    """
    if not term_name or term_name == "Unknown":
        return False

    term_lower = term_name.lower()
    current_lower = current_semester.lower()
    if current_lower in term_lower:
        return True

    reference_date = date.today() if today is None else today
    year_short = str(reference_date.year)[-2:]
    if year_short not in term_lower:
        return False

    return bool(
        ("fall" in current_lower and "f" in term_lower)
        or ("winter" in current_lower and "w" in term_lower)
        or ("spring" in current_lower and ("sp" in term_lower or "s" in term_lower))
        or (
            "summer" in current_lower
            and ("u" in term_lower or "sum" in term_lower or "s" in term_lower)
        )
    )


def build_semester_choices(
    years_back: int = 2,
    years_forward: int = 1,
    *,
    today: date | None = None,
    seasons: Sequence[str] = DEFAULT_SEASONS,
) -> list[str]:
    """Build a chronological list of semester strings around ``today``."""
    reference_date = date.today() if today is None else today
    start_year = reference_date.year - years_back
    end_year = reference_date.year + years_forward

    return [
        f"{year} {season}"
        for year in range(start_year, end_year + 1)
        for season in seasons
    ]


def semester_matches_term(semester: str, term_name: str | None) -> bool:
    """Check whether a chosen ``semester`` matches a Canvas ``term_name``."""
    if not term_name or term_name == "Unknown":
        return False

    term_lower = term_name.lower()
    semester_lower = semester.lower()
    if semester_lower in term_lower:
        return True

    parts = semester.split()
    if len(parts) != 2:
        return False
    year_string, season = parts
    year_short = year_string[-2:]

    season_abbreviation_checks = {
        "fall": lambda text: "f" in text,
        "winter": lambda text: "w" in text,
        "spring": lambda text: "sp" in text or "s" in text,
        "summer": lambda text: "u" in text or "sum" in text or "s" in text,
    }

    checker = season_abbreviation_checks.get(season.lower())
    if checker is None:
        return False
    return checker(term_lower) and year_short in term_lower
