"""
Grade Validator
===============
Ensures numeric integrity of grades being imported, handles scaling,
and performs smoke tests to catch common mistakes.

"""

import logging
import math
from typing import Any

# Get logger for this module
logger = logging.getLogger(__name__)


# What NetAcad writes in a cell that holds no score.
BLANK_SCORE_MARKERS = frozenset({"", "-", "n/a", "null", "none", "nan"})


class GradeValidator:
    """
    Validates and transforms grade data from NetAcad to Canvas format.
    """

    def has_recorded_score(self, raw_value: Any) -> bool:
        """
        Tell a score NetAcad actually recorded from a cell it left empty.

        NetAcad leaves a cell blank (or shows '-', 'N/A') when the student
        has not attempted that item yet, which includes every exam that is
        not due.  A blank cell is "no score", never a zero: an explicit
        '0' or '0.0' is a recorded score and is still imported.

        Args:
            raw_value: The raw value from the NetAcad export.

        Returns:
            True when the cell holds a score, False when it is empty.
        """
        if raw_value is None:
            return False
        if isinstance(raw_value, float) and math.isnan(raw_value):
            return False
        return str(raw_value).strip().lower() not in BLANK_SCORE_MARKERS

    def parse_numeric_score(self, raw_value: Any) -> float:
        """
        Convert various NetAcad score formats into a float.

        Handles: '-', blank, 'N/A', 'null' -> 0.0
        Handles strings with percentages or other noise.

        Args:
            raw_value: The raw value from the NetAcad export.

        Returns:
            A float representing the score.
        """
        if raw_value is None:
            return 0.0

        # pandas represents missing cells as float('nan') even with
        # dtype=str — catch that before string conversion.
        if isinstance(raw_value, float) and math.isnan(raw_value):
            return 0.0

        str_val = str(raw_value).strip().lower()
        if str_val in BLANK_SCORE_MARKERS:
            return 0.0

        # Remove percentage sign if present
        str_val = str_val.replace("%", "")

        try:
            return float(str_val)
        except ValueError:
            logger.warning(f"Could not parse score '{raw_value}', defaulting to 0.0")
            return 0.0

    def scale_score_half_up(self, score: float, netacad_max: float, canvas_max: float) -> float:
        """
        Scale a score from NetAcad points to Canvas points using half-up rounding.

        Python's built-in round() uses banker's rounding (rounds 0.5 to nearest even).
        We want 0.5 to always round up to 1.

        Args:
            score: The raw score from NetAcad.
            netacad_max: The points possible in NetAcad.
            canvas_max: The points possible in Canvas.

        Returns:
            The scaled score, rounded half-up.
        """
        if netacad_max == 0 or math.isnan(score):
            return 0.0

        if netacad_max == canvas_max:
            return score

        raw_scaled = (score * canvas_max) / netacad_max

        # Implement half-up rounding
        return float(math.floor(raw_scaled + 0.5))

    def perform_smoke_tests(self, grades: list[float]) -> tuple[bool, str]:
        """
        Check if the set of grades looks suspicious.

        Primary test: All-zeros test. If more than 80% of scores are 0,
        it likely indicates a mismatch between the roster and the export.

        Args:
            grades: List of numeric scores to check.

        Returns:
            Tuple of (is_passed, message).
        """
        if not grades:
            return True, "No grades to validate."

        zero_count = sum(1 for g in grades if g <= 0)
        zero_percentage = (zero_count / len(grades)) * 100

        if zero_percentage > 80:
            return False, (
                f"Suspiciously high number of zeros ({zero_percentage:.1f}%). "
                "This often happens if the wrong NetAcad file is paired with this course roster."
            )

        return True, "Smoke tests passed."
