"""Sanitized fixtures mirror observed report headers, never real student rows."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from coursewright.ta.zybooks.models import RetrievalRequest, RetrievedReport
from coursewright.ta.zybooks.parser import ZyBooksReportError, validate_report


@pytest.fixture
def report():
    request = RetrievalRequest("ExampleBook", (12,), datetime(2026, 9, 8, tzinfo=UTC))
    headers = (
        "Last name,First name,Primary email,Class section,Total (3.0),"
        "Participation total (0),Challenge total (0),Lab total (3.0),"
        "2.12 - Participation (0),2.12 - Challenge (0),2.12 - Lab (3.0)\n"
    )
    records = "Example,Student,example@example.invalid,,100.0,0,0,100.0,N/A,N/A,100.0\n"
    return RetrievedReport(
        request,
        42,
        (headers + records).encode(),
        {
            "ordering": {
                "content_ordering": {
                    "chapters": [
                        {
                            "number": 2,
                            "sections": [{"number": 12, "canonical_section_id": 12}],
                        }
                    ]
                }
            }
        },
        {
            "data": {
                "2": {
                    "12": {
                        "100": {
                            "activity_type": "lab",
                            "max_score": 3.0,
                            "optional": False,
                            "resource_type": "zystudio",
                        }
                    }
                }
            }
        },
        {
            "roster": {
                "Instructor": [],
                "Student": [
                    {
                        "user_id": 1,
                        "first_name": "Student",
                        "last_name": "Example",
                        "primary_email": "example@example.invalid",
                    }
                ],
                "Temporary": [],
            }
        },
    )


def test_validated_report_preserves_stable_lab_identity_and_source_values(report):
    result = validate_report(report)
    assert result.student_count == result.section_count == 1
    assert len(result.scores) == 3
    lab = result.scores[-1]
    assert lab.lab_id == 100
    assert lab.possible_points == Decimal("3")
    assert lab.percentage == Decimal("100")
    assert result.scores[0].percentage is None
    assert "example@example.invalid" not in repr(result)


@pytest.mark.parametrize("value", ["", "N/A", "bad", "NaN", "Infinity", "-1", "101"])
def test_invalid_or_missing_lab_scores_never_become_zero(report, value):
    changed = report.report_bytes.rsplit(b",", 1)[0] + b"," + value.encode() + b"\n"
    with pytest.raises(ZyBooksReportError):
        validate_report(replace(report, report_bytes=changed))


def test_missing_student_is_incomplete_not_zero(report):
    changed = report.report_bytes.splitlines(keepends=True)[0]
    with pytest.raises(ZyBooksReportError, match="missing roster"):
        validate_report(replace(report, report_bytes=changed))


def test_temporary_students_are_part_of_complete_coverage(report):
    report.roster["roster"]["Temporary"] = report.roster["roster"].pop("Student")
    report.roster["roster"]["Student"] = []
    assert validate_report(report).student_count == 1


def test_two_labs_in_a_section_require_individual_retrieval(report):
    report.activity_information["data"]["2"]["12"]["101"] = {
        "activity_type": "lab",
        "max_score": 3,
        "optional": False,
    }
    with pytest.raises(ZyBooksReportError, match="multiple labs"):
        validate_report(report)


def test_wrong_possible_points_are_schema_drift(report):
    report.activity_information["data"]["2"]["12"]["100"]["max_score"] = 4
    with pytest.raises(ZyBooksReportError, match="metadata"):
        validate_report(report)


def test_duplicate_student_record_is_rejected(report):
    changed = report.report_bytes + report.report_bytes.splitlines(keepends=True)[1]
    with pytest.raises(ZyBooksReportError, match="duplicate"):
        validate_report(replace(report, report_bytes=changed))


def test_missing_category_column_is_rejected(report):
    changed = report.report_bytes.replace(b",2.12 - Challenge (0)", b"")
    with pytest.raises(ZyBooksReportError):
        validate_report(replace(report, report_bytes=changed))


def test_rounded_source_percentage_is_not_fabricated_into_exact_points(report):
    changed = report.report_bytes.replace(b"100.0", b"33.3")
    result = validate_report(replace(report, report_bytes=changed))
    assert result.scores[-1].percentage == Decimal("33.3")
    assert not hasattr(result.scores[-1], "earned_points")
