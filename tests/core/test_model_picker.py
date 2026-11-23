"""Targeted tests for the model picker's pricing staleness warning.

Ported from discussorator ``tests/test_model_picker.py``, which
mounted the Textual ``ModelPickerScreen`` and read its banner.  The
screen belongs to ``ui/tui`` (a later card); the rule it displayed
(pricing older than 30 days is stale) is library logic and lives in
``core/llm/model_registry`` (AGENTS.md 3.7).  These tests cover that
rule; the screen test follows with the screen.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from coursewright.core.llm import model_registry
from coursewright.core.llm.model_registry import (
    get_pricing_age_in_days,
    get_pricing_staleness_warning,
    get_pricing_updated_at,
)


def test_stale_pricing_date_produces_warning() -> None:
    """Pricing more than 30 days old yields the warning with its age."""
    stale_pricing_date = date.today() - timedelta(days=45)
    with patch.object(
        model_registry, "get_pricing_updated_at", return_value=stale_pricing_date
    ):
        warning = get_pricing_staleness_warning()

    assert warning is not None
    assert "45 days old" in warning


def test_fresh_pricing_date_produces_no_warning() -> None:
    """Exactly 30 days is still fresh: the source rule is `> 30`."""
    today = get_pricing_updated_at() + timedelta(days=30)

    assert get_pricing_age_in_days(today) == 30
    assert get_pricing_staleness_warning(today) is None
    assert get_pricing_staleness_warning(today + timedelta(days=1)) is not None
