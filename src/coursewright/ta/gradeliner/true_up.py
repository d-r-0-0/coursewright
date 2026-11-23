"""NetAcad grade true-up preview (library form of the old cantrip).

Unlike the normal import (which skips any submission Canvas already
considers graded), the true-up compares EVERY mapped (student,
assignment) pair: the scaled NetAcad score against the current Canvas
score.  The result is grouped into increases, decreases and matches so
the instructor can approve a true-up before any write happens.

READ-ONLY GUARANTEE: this module never calls ``submit_grade`` and never
writes the mapping caches.  It returns data and raises exceptions; the
interface decides how to show them (AGENTS.md 3.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from coursewright.core.canvas.client import CanvasClient
from coursewright.ta.gradeliner.mapper import MappingManager
from coursewright.ta.gradeliner.parser import NetacadExportParser
from coursewright.ta.gradeliner.validator import GradeValidator

# Canvas scores are compared to the nearest hundredth of a point.
SCORE_AGREEMENT_TOLERANCE = 0.005


class TrueUpError(Exception):
    """The preview cannot be built (missing or mismatched mappings)."""


@dataclass(frozen=True)
class TrueUpRow:
    """One (student, assignment) comparison."""

    student_name: str
    assignment_name: str
    canvas_score: float | None
    scaled_netacad_score: float
    workflow_state: str
    raw_netacad_score: float
    assignment_id: int
    canvas_user_id: int


@dataclass
class TrueUpPreview:
    """Everything the true-up preview found, grouped for review."""

    course_id: int
    course_code: str
    increases: list[TrueUpRow] = field(default_factory=list)
    decreases: list[TrueUpRow] = field(default_factory=list)
    matches: list[TrueUpRow] = field(default_factory=list)
    unmatched_netacad_names: list[str | None] = field(default_factory=list)
    matched_student_count: int = 0
    mapped_students_missing_from_export: list[str] = field(default_factory=list)


def build_true_up_preview(
    canvas_client: CanvasClient,
    course_id: int,
    netacad_csv: Path,
    export_root: Path | None = None,
) -> TrueUpPreview:
    """Compare a NetAcad export with Canvas for one course, read-only.

    Args:
        canvas_client: Connected Canvas client (only reads are made).
        course_id: The Canvas course whose cached mappings are used.
        netacad_csv: The NetAcad gradebook export.
        export_root: Where the mapping caches live (see ``MappingManager``).

    Raises:
        TrueUpError: the course is unknown, has no cached mappings, or the
            cached mappings belong to a different course.
    """
    course = canvas_client.get_course(course_id)
    if not course:
        raise TrueUpError(f"Could not find course with ID {course_id}")
    course_code = course.get("course_code") or course.get("name") or f"Course_{course_id}"

    mapper = MappingManager(course_code, llm_client=None, export_root=export_root)
    assignment_cache = mapper.load_assignment_cache()
    student_cache = mapper.load_student_cache()
    if not assignment_cache or not student_cache:
        raise TrueUpError("No cached gradeliner mappings for this course.")
    if assignment_cache.course_id != course_id:
        raise TrueUpError("Cached mapping belongs to a different course ID.")

    netacad_parser = NetacadExportParser()
    netacad_parser.parse_export_file(netacad_csv)
    records = netacad_parser.extract_clean_student_records()
    validator = GradeValidator()

    student_by_email = {s.netacad_email.lower(): s for s in student_cache.students}
    student_by_name = {s.netacad_name.lower(): s for s in student_cache.students}

    # Current Canvas scores for every mapped assignment, keyed by
    # (assignment_id, user_id).  workflow_state distinguishes a real zero
    # from a never-graded placeholder.
    current: dict[tuple[int, int], tuple[float | None, str]] = {}
    for mapping in assignment_cache.mappings:
        for submission in canvas_client.get_assignment_submissions(
            course_id, mapping.canvas_assignment_id
        ):
            current[(mapping.canvas_assignment_id, submission.user_id)] = (
                submission.score,
                submission.workflow_state,
            )

    preview = TrueUpPreview(course_id=course_id, course_code=course_code)
    matched_netacad_names: set[str] = set()
    for record in records:
        email = str(record.get("EMAIL", "") or "").lower()
        name = str(record.get("NAME", "") or "").lower()
        student = student_by_email.get(email) or student_by_name.get(name)
        if not student:
            preview.unmatched_netacad_names.append(record.get("NAME"))
            continue
        matched_netacad_names.add(student.netacad_name)
        for mapping in assignment_cache.mappings:
            raw = validator.parse_numeric_score(record.get(mapping.netacad_column))
            scaled = validator.scale_score_half_up(
                raw,
                mapping.netacad_points_possible,
                mapping.canvas_points_possible,
            )
            score, state = current.get(
                (mapping.canvas_assignment_id, student.canvas_user_id),
                (None, "missing"),
            )
            row = TrueUpRow(
                student_name=student.canvas_name,
                assignment_name=mapping.canvas_assignment_name,
                canvas_score=score,
                scaled_netacad_score=scaled,
                workflow_state=state,
                raw_netacad_score=raw,
                assignment_id=mapping.canvas_assignment_id,
                canvas_user_id=student.canvas_user_id,
            )
            canvas_score = score or 0.0
            if abs(canvas_score - scaled) < SCORE_AGREEMENT_TOLERANCE:
                preview.matches.append(row)
            elif scaled > canvas_score:
                preview.increases.append(row)
            else:
                preview.decreases.append(row)

    preview.matched_student_count = len(matched_netacad_names)
    preview.mapped_students_missing_from_export = [
        s.netacad_name
        for s in student_cache.students
        if s.netacad_name not in matched_netacad_names
    ]
    return preview
