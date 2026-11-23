"""Explicit scope for a read-only zyBooks retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class RetrievalRequest:
    """Bind a report to one book, unique stable section IDs, and an instant.

    Callers capture the current time once per fetch. A timezone-aware cutoff
    avoids silently inheriting the Reporting page's end-of-day default.
    """

    book_id: str
    section_ids: tuple[int, ...]
    cutoff: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.book_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.book_id):
            raise ValueError("Supply a zyBook ID, not a URL or path.")
        if (
            not isinstance(self.section_ids, tuple)
            or not self.section_ids
            or any(
                type(section_id) is not int or section_id <= 0 for section_id in self.section_ids
            )
            or len(set(self.section_ids)) != len(self.section_ids)
        ):
            raise ValueError("Supply unique positive integer section IDs.")
        if (
            not isinstance(self.cutoff, datetime)
            or self.cutoff.tzinfo is None
            or self.cutoff.utcoffset() is None
        ):
            raise ValueError("The retrieval cutoff must include a timezone.")
        if self.cutoff.second or self.cutoff.microsecond:
            raise ValueError("Reporting supports minute precision; supply an exact minute cutoff.")


@dataclass(frozen=True)
class RetrievedReport:
    """One browser download associated with its generation request and job.

    Sensitive exports and roster metadata are excluded from repr. This is a
    retrieval result, not approval to calculate or publish Canvas grades.
    """

    request: RetrievalRequest
    report_id: int
    report_bytes: bytes = field(repr=False)
    ordering: dict = field(repr=False)
    activity_information: dict = field(repr=False)
    roster: dict = field(repr=False)
