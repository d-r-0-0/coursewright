"""Tests for semester detection and term matching (ported from discussorator)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from coursewright.core.config.semester import (
    build_semester_choices,
    detect_current_semester,
    is_current_semester,
    semester_matches_term,
)


class TestDetectCurrentSemester:
    """Verify date-to-semester mapping."""

    @patch("coursewright.core.config.semester.date")
    def test_january_is_winter(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 1, 15)
        assert detect_current_semester() == "2026 Winter"

    @patch("coursewright.core.config.semester.date")
    def test_april_is_winter(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 4, 30)
        assert detect_current_semester() == "2026 Winter"

    @patch("coursewright.core.config.semester.date")
    def test_may_is_summer(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 5, 1)
        assert detect_current_semester() == "2026 Summer"

    @patch("coursewright.core.config.semester.date")
    def test_august_is_summer(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 8, 31)
        assert detect_current_semester() == "2026 Summer"

    @patch("coursewright.core.config.semester.date")
    def test_september_is_fall(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 9, 1)
        assert detect_current_semester() == "2026 Fall"

    @patch("coursewright.core.config.semester.date")
    def test_december_is_fall(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 12, 15)
        assert detect_current_semester() == "2026 Fall"

    def test_custom_seasons_override_default_term_names(self) -> None:
        custom_seasons = ("Spring", "Summer", "Fall")
        assert (
            detect_current_semester(date(2026, 2, 1), seasons=custom_seasons)
            == "2026 Spring"
        )

    def test_invalid_seasons_length_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="exactly three"):
            detect_current_semester(date(2026, 2, 1), seasons=("Fall", "Spring"))


class TestIsCurrentSemester:
    """Verify term-name matching against current semester."""

    def test_exact_match(self) -> None:
        assert is_current_semester("2026 Winter", "2026 Winter") is True

    def test_substring_match(self) -> None:
        assert is_current_semester("2026 Winter Semester", "2026 Winter") is True

    def test_no_match(self) -> None:
        assert is_current_semester("2025 Fall", "2026 Winter") is False

    def test_none_term_returns_false(self) -> None:
        assert is_current_semester(None, "2026 Winter") is False

    def test_unknown_term_returns_false(self) -> None:
        assert is_current_semester("Unknown", "2026 Winter") is False

    @patch("coursewright.core.config.semester.date")
    def test_abbreviated_fall(self, mock_date) -> None:
        mock_date.today.return_value = date(2025, 10, 1)
        assert is_current_semester("F25", "2025 Fall") is True

    @patch("coursewright.core.config.semester.date")
    def test_abbreviated_winter(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 2, 1)
        assert is_current_semester("W26", "2026 Winter") is True

    def test_abbreviated_spring_and_summer(self) -> None:
        assert is_current_semester("SP26", "2026 Spring", today=date(2026, 3, 1)) is True
        assert is_current_semester("SU26", "2026 Summer", today=date(2026, 6, 1)) is True


class TestBuildSemesterChoices:
    """Verify the chronological semester list builder."""

    @patch("coursewright.core.config.semester.date")
    def test_default_range_contains_current(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 2, 10)
        choices = build_semester_choices()
        assert "2026 Winter" in choices

    @patch("coursewright.core.config.semester.date")
    def test_choices_are_chronological(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 2, 10)
        choices = build_semester_choices()
        assert choices[0] == "2024 Winter"
        assert choices[-1] == "2027 Fall"

    @patch("coursewright.core.config.semester.date")
    def test_each_year_has_three_seasons(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 6, 1)
        choices = build_semester_choices(years_back=0, years_forward=0)
        assert choices == ["2026 Winter", "2026 Summer", "2026 Fall"]

    @patch("coursewright.core.config.semester.date")
    def test_custom_range(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 1, 1)
        choices = build_semester_choices(years_back=1, years_forward=0)
        assert len(choices) == 6
        assert choices[0] == "2025 Winter"
        assert choices[-1] == "2026 Fall"

    def test_custom_seasons_order(self) -> None:
        choices = build_semester_choices(
            years_back=0,
            years_forward=0,
            today=date(2026, 5, 1),
            seasons=("Spring", "Summer", "Fall"),
        )
        assert choices == ["2026 Spring", "2026 Summer", "2026 Fall"]


class TestSemesterMatchesTerm:
    """Verify matching any semester against Canvas term names."""

    def test_exact_match(self) -> None:
        assert semester_matches_term("2025 Fall", "2025 Fall") is True

    def test_substring_match(self) -> None:
        assert semester_matches_term("2025 Fall", "2025 Fall Semester") is True

    def test_no_match(self) -> None:
        assert semester_matches_term("2025 Fall", "2026 Winter") is False

    def test_none_term_returns_false(self) -> None:
        assert semester_matches_term("2025 Fall", None) is False

    def test_unknown_term_returns_false(self) -> None:
        assert semester_matches_term("2025 Fall", "Unknown") is False

    def test_abbreviated_fall(self) -> None:
        assert semester_matches_term("2025 Fall", "F25") is True

    def test_abbreviated_winter(self) -> None:
        assert semester_matches_term("2026 Winter", "W26") is True

    def test_abbreviated_summer(self) -> None:
        assert semester_matches_term("2025 Summer", "SU25") is True

    def test_abbreviated_spring(self) -> None:
        assert semester_matches_term("2026 Spring", "SP26") is True

    def test_different_year_no_match(self) -> None:
        assert semester_matches_term("2025 Fall", "F24") is False

    def test_malformed_semester_returns_false(self) -> None:
        assert semester_matches_term("bad", "F25") is False

    def test_unrecognized_season_returns_false(self) -> None:
        assert semester_matches_term("2026 Autumn", "A26") is False
