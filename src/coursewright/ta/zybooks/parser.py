"""Inspect report structure without interpreting unverified grade columns."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from coursewright.ta.zybooks.models import RetrievedReport
from coursewright.ta.zybooks.session import MAX_REPORT_BYTES


class ZyBooksReportError(ValueError):
    """A report cannot be inspected safely using the supported CSV structure."""


@dataclass(frozen=True)
class ReportStructure:
    """Column labels and record count only; never retain student records."""

    columns: tuple[str, ...]
    record_count: int


def inspect_report(report_bytes: bytes) -> ReportStructure:
    """Read standard UTF-8 CSV, preserving headers without guessing semantics.

    Metadata preambles, repeated/empty headings and inconsistent row lengths
    require inspection of the actual format before grade parsing is implemented.
    A well-formed CSV alone does not establish that a report is complete.
    """
    if not report_bytes or len(report_bytes) > MAX_REPORT_BYTES:
        raise ZyBooksReportError("Report is empty or exceeds the 20 MiB limit.")
    try:
        report_text = report_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ZyBooksReportError("Report is not UTF-8 CSV; its format needs inspection.") from None
    if "\x00" in report_text:
        raise ZyBooksReportError("Report contains unexpected binary data.")

    try:
        reader = csv.reader(io.StringIO(report_text, newline=""), strict=True)
        columns = tuple(next(reader, ()))
        normalized_columns = tuple(column.strip().casefold() for column in columns)
        if (
            len(columns) < 2
            or not all(normalized_columns)
            or len(set(normalized_columns)) != len(columns)
        ):
            raise ZyBooksReportError("Expected a CSV header with unique, nonempty columns.")
        record_count = 0
        for record in reader:
            if not record:
                continue
            if len(record) != len(columns):
                raise ZyBooksReportError("CSV rows do not match the header; no grades were parsed.")
            record_count += 1
    except csv.Error:
        raise ZyBooksReportError("Report CSV is malformed; no grades were parsed.") from None

    return ReportStructure(columns=columns, record_count=record_count)


@dataclass(frozen=True)
class ReportedScore:
    """A source percentage, preserving CSV rounding rather than inventing points.

    Per-section lab scores have a stable lab ID only when metadata proves that
    exactly one lab contributed. Percentages alone do not prove attempt policy.
    """

    student_id: int = field(repr=False)
    section_id: int
    category: str
    possible_points: Decimal
    percentage: Decimal | None
    lab_id: int | None = None


@dataclass(frozen=True)
class ValidatedReport:
    """Coverage-checked source data; not yet an exact-points grading contract."""

    report_id: int
    student_count: int
    section_count: int
    scores: tuple[ReportedScore, ...] = field(repr=False)


def validate_report(report: RetrievedReport) -> ValidatedReport:
    """Check roster, stable IDs, category coverage and possible-point metadata.

    No malformed/missing value becomes zero. Multiple labs in one section are
    explicitly unsupported by the section-level CSV. Its rounded percentages
    cannot be represented as exact earned points; callers must not use this
    result as the future best-five grading input without additional evidence.
    """
    try:
        return _validate_report(report)
    except ZyBooksReportError as error:
        raise error from None
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ZyBooksReportError("Unsupported zyBooks report or metadata schema.") from None


def _validate_report(report: RetrievedReport) -> ValidatedReport:
    structure = inspect_report(report.report_bytes)
    chapters = report.ordering["ordering"]["content_ordering"]["chapters"]
    sections = {
        section["canonical_section_id"]: (str(chapter["number"]), str(section["number"]))
        for chapter in chapters
        for section in chapter["sections"]
    }
    if not set(report.request.section_ids) <= sections.keys():
        raise ZyBooksReportError("Report scope references sections outside this book.")
    metadata = report.activity_information["data"]
    expected_columns = {}
    category_totals = {category: Decimal(0) for category in ("Participation", "Challenge", "Lab")}
    for section_id in report.request.section_ids:
        chapter_number, section_number = sections[section_id]
        resources = metadata[chapter_number][section_number]
        for category in category_totals:
            matching = [
                (resource_id, resource)
                for resource_id, resource in resources.items()
                if resource["activity_type"] == category.lower() and not resource["optional"]
            ]
            if category == "Lab" and len(matching) > 1:
                raise ZyBooksReportError(
                    "A section combines multiple labs; individual-lab retrieval is required."
                )
            point_key = "max_score" if category == "Lab" else "parts"
            possible = sum(
                (Decimal(str(resource[point_key])) for _, resource in matching), Decimal(0)
            )
            if not possible.is_finite() or possible < 0:
                raise ZyBooksReportError("Invalid activity point metadata.")
            lab_id = int(matching[0][0]) if category == "Lab" and matching else None
            label = f"{chapter_number}.{section_number} - {category}"
            expected_columns[label] = (section_id, category, possible, lab_id)
            category_totals[category] += possible
    roster = report.roster["roster"]
    if set(roster) - {"Instructor", "Student", "Temporary"}:
        raise ZyBooksReportError("Unknown roster groups; report coverage needs inspection.")
    students = roster["Student"] + roster["Temporary"]
    identities = {}
    student_ids = set()
    for student in students:
        email = student["primary_email"].strip().casefold()
        student_id = student["user_id"]
        if (
            not email
            or email in identities
            or type(student_id) is not int
            or student_id in student_ids
        ):
            raise ZyBooksReportError("Roster identities are missing or ambiguous.")
        identities[email] = student
        student_ids.add(student_id)
    identity_headers = ("Last name", "First name", "Primary email", "Class section")
    if structure.columns[:4] != identity_headers:
        raise ZyBooksReportError("Unsupported report identity columns.")
    totals = {f"{category} total": possible for category, possible in category_totals.items()}
    totals["Total"] = sum(category_totals.values())
    parsed_columns = {}
    for index, header in enumerate(structure.columns[4:], 4):
        match = re.fullmatch(r"(.+) \(([0-9]+(?:\.[0-9]+)?)\)", header)
        if not match:
            raise ZyBooksReportError("Unsupported score header.")
        label, possible_text = match.groups()
        expected = totals.get(label)
        if label in expected_columns:
            expected = expected_columns[label][2]
        if expected is None or Decimal(possible_text) != expected or label in parsed_columns:
            raise ZyBooksReportError("Score columns do not match the requested activity metadata.")
        parsed_columns[label] = index
    if parsed_columns.keys() != totals.keys() | expected_columns.keys():
        raise ZyBooksReportError("Report is missing requested category or section columns.")
    rows = csv.reader(io.StringIO(report.report_bytes.decode("utf-8-sig")), strict=True)
    next(rows)
    seen_emails = set()
    scores = []
    for row in rows:
        if not row:
            continue
        email = row[2].strip().casefold()
        if email not in identities or email in seen_emails:
            raise ZyBooksReportError("Report contains duplicate or unexpected student identities.")
        student = identities[email]
        if row[0] != student["last_name"] or row[1] != student["first_name"]:
            raise ZyBooksReportError("Report identity does not match the roster.")
        seen_emails.add(email)
        for label, index in parsed_columns.items():
            possible = totals[label] if label in totals else expected_columns[label][2]
            percentage = _read_percentage(row[index], possible, is_total=label in totals)
            if label in expected_columns:
                section_id, category, _, lab_id = expected_columns[label]
                scores.append(
                    ReportedScore(
                        student["user_id"], section_id, category, possible, percentage, lab_id
                    )
                )
    if seen_emails != identities.keys():
        raise ZyBooksReportError("Report is missing roster students; incomplete data is not zero.")
    return ValidatedReport(
        report.report_id, len(identities), len(report.request.section_ids), tuple(scores)
    )


def _read_percentage(value: str, possible: Decimal, *, is_total: bool = False) -> Decimal | None:
    if possible == 0:
        expected_marker = "0" if is_total else "N/A"
        if value.strip() != expected_marker:
            raise ZyBooksReportError("Unexpected marker for a category with no possible points.")
        return None
    try:
        percentage = Decimal(value)
    except InvalidOperation:
        raise ZyBooksReportError("Missing or invalid score; no zero was substituted.") from None
    if not percentage.is_finite() or not 0 <= percentage <= 100:
        raise ZyBooksReportError("Score percentage is outside its supported range.")
    return percentage
