"""Orchestration for Packet Tracer submission completeness checking, feedback, and grading.

Silent library code only (AGENTS.md 3.8): returns structured summaries and
results, raises domain errors, and delegates all Canvas mutations to
:class:`SafeCanvasWriter`.  Never prints or renders Rich output.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coursewright.core.canvas.safe_write import SafeCanvasWriter
from coursewright.ta.packettracer.classifier import (
    COMPLETENESS_COMMENT_MARKER,
    COMPLETENESS_FEEDBACK_MARKER,
    COMPLETENESS_STATUS_MARKER,
    CompletenessReport,
    CompletenessVerdict,
    SubmissionCompletenessChecker,
    build_submission_feedback_comment,
    build_submission_status_comment,
    default_templates_directory,
)

logger = logging.getLogger(__name__)

REQUIRED_FEEDBACK_TEMPLATES: tuple[str, ...] = (
    "missing_submission.md",
    "missing_lab_document.md",
    "missing_packet_tracer.md",
)


class MissingTemplatesError(RuntimeError):
    """Raised when workspace feedback templates are missing and fallback is disallowed."""


@dataclass
class PacketTracerCheckSummary:
    """Completeness summary for one Packet Tracer assignment."""

    course_id: int
    assignment_id: int
    assignment_name: str
    points_possible: float
    reports: list[CompletenessReport] = field(default_factory=list)
    complete_count: int = 0
    incomplete_count: int = 0
    partial_count: int = 0
    not_submitted_count: int = 0
    no_lab_files_count: int = 0
    templates_found: bool = False


@dataclass(frozen=True)
class AutoGradeOutcome:
    """Per-student outcome from auto-grading complete submissions."""

    user_id: int
    student_name: str
    status: str
    score: float | None = None
    comment: str | None = None
    backup_path: str | None = None
    error: str | None = None


@dataclass
class AutoGradeResult:
    """Batch outcome from auto-grading complete submissions."""

    course_id: int
    assignment_id: int
    assignment_name: str
    points_possible: float
    execute: bool
    graded_count: int = 0
    planned_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    outcomes: list[AutoGradeOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FeedbackPostOutcome:
    """Per-student outcome from posting two-part completeness feedback."""

    user_id: int
    student_name: str
    status: str
    comments_posted: int = 0
    comments_planned: int = 0
    replaced_comment_ids: tuple[int, ...] = ()
    status_comment: str = ""
    feedback_comment: str | None = None
    backup_paths: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass
class FeedbackPostResult:
    """Batch outcome from posting two-part completeness feedback comments."""

    course_id: int
    assignment_id: int
    assignment_name: str
    execute: bool
    posted_count: int = 0
    planned_count: int = 0
    replaced_count: int = 0
    would_replace_count: int = 0
    failed_count: int = 0
    used_generic_fallback: bool = False
    outcomes: list[FeedbackPostOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def templates_available(templates_dir: Path | None = None) -> bool:
    """Return True if at least one required feedback template exists and is non-empty."""
    resolved_dir = default_templates_directory() if templates_dir is None else templates_dir
    if resolved_dir is None or not resolved_dir.is_dir():
        return False
    for template_name in REQUIRED_FEEDBACK_TEMPLATES:
        candidate = resolved_dir / template_name
        try:
            if candidate.is_file() and candidate.read_text(encoding="utf-8").strip():
                return True
        except OSError:
            continue
    return False


def _extract_assignment_metadata(assignment: Any, assignment_id: int) -> tuple[str, float]:
    """Return ``(assignment_name, points_possible)`` from an Assignment or dict."""
    if assignment is None:
        return f"Assignment {assignment_id}", 100.0
    if isinstance(assignment, dict):
        name = assignment.get("name") or f"Assignment {assignment_id}"
        raw_points = assignment.get("points_possible")
    else:
        name = getattr(assignment, "name", None) or f"Assignment {assignment_id}"
        raw_points = getattr(assignment, "points_possible", None)
    points_possible = float(raw_points) if raw_points else 100.0
    return str(name), points_possible


def check_packet_tracer_assignment(
    canvas_client: Any,
    course_id: int,
    assignment_id: int,
    *,
    assignment: Any = None,
    templates_dir: Path | None = None,
) -> PacketTracerCheckSummary:
    """Fetch submissions for one assignment and summarize file completeness."""
    if assignment is None:
        for candidate in canvas_client.get_assignments(course_id):
            candidate_id = (
                candidate.get("id")
                if isinstance(candidate, dict)
                else getattr(candidate, "id", None)
            )
            if candidate_id == assignment_id:
                assignment = candidate
                break

    assignment_name, points_possible = _extract_assignment_metadata(assignment, assignment_id)
    submissions = canvas_client.get_assignment_submissions(course_id, assignment_id)
    all_reports = SubmissionCompletenessChecker().check_all(submissions)
    reports = [report for report in all_reports if report.user_id]

    complete_count = sum(1 for r in reports if r.verdict == CompletenessVerdict.COMPLETE)
    partial_count = sum(1 for r in reports if r.verdict == CompletenessVerdict.PARTIAL)
    not_submitted_count = sum(1 for r in reports if r.verdict == CompletenessVerdict.NOT_SUBMITTED)
    no_lab_files_count = sum(1 for r in reports if r.verdict == CompletenessVerdict.NO_LAB_FILES)
    incomplete_count = len(reports) - complete_count

    return PacketTracerCheckSummary(
        course_id=course_id,
        assignment_id=assignment_id,
        assignment_name=assignment_name,
        points_possible=points_possible,
        reports=reports,
        complete_count=complete_count,
        incomplete_count=incomplete_count,
        partial_count=partial_count,
        not_submitted_count=not_submitted_count,
        no_lab_files_count=no_lab_files_count,
        templates_found=templates_available(templates_dir),
    )


def fetch_existing_completeness_comments(
    canvas_client: Any,
    course_id: int,
    assignment_ids: Iterable[int],
) -> dict[tuple[int, int], dict[str, list[int]]]:
    """Return existing completeness comment IDs grouped by marker type.

    Structure::

        {(assignment_id, user_id): {
            "status":   [comment_id, ...],   # COMPLETENESS_STATUS_MARKER
            "feedback": [comment_id, ...],   # COMPLETENESS_FEEDBACK_MARKER
            "legacy":   [comment_id, ...],   # old COMPLETENESS_COMMENT_MARKER
        }}
    """
    existing: dict[tuple[int, int], dict[str, list[int]]] = {}
    for assignment_id in assignment_ids:
        try:
            submissions = canvas_client.get_submissions_with_comments(
                course_id,
                assignment_id,
            )
            for sub in submissions:
                for comment in sub.submission_comments:
                    body = comment.comment
                    key = (assignment_id, sub.user_id)
                    buckets = existing.setdefault(
                        key,
                        {"status": [], "feedback": [], "legacy": []},
                    )
                    if COMPLETENESS_STATUS_MARKER in body:
                        buckets["status"].append(comment.id)
                    elif COMPLETENESS_FEEDBACK_MARKER in body:
                        buckets["feedback"].append(comment.id)
                    elif COMPLETENESS_COMMENT_MARKER in body:
                        buckets["legacy"].append(comment.id)
        except Exception as fetch_err:
            logger.warning(
                "Could not fetch comments for assignment %d: %s",
                assignment_id,
                fetch_err,
            )
    return existing


def auto_grade_complete_submissions(
    canvas_client: Any,
    writer: SafeCanvasWriter,
    course_id: int,
    assignment_id: int,
    assignment_name: str,
    points_possible: float,
    reports: list[CompletenessReport],
    *,
    already_graded_user_ids: set[int] | None = None,
    execute: bool = False,
    on_progress: Callable[[int, int, CompletenessReport], None] | None = None,
) -> AutoGradeResult:
    """Auto-grade COMPLETE submissions at 100%, skipping already-graded students."""
    result = AutoGradeResult(
        course_id=course_id,
        assignment_id=assignment_id,
        assignment_name=assignment_name,
        points_possible=points_possible,
        execute=execute,
    )

    complete_reports = [
        report
        for report in reports
        if report.user_id and report.verdict == CompletenessVerdict.COMPLETE
    ]
    if not complete_reports:
        return result

    if already_graded_user_ids is None:
        already_graded_user_ids = set()
        try:
            subs = canvas_client.get_assignment_submissions(course_id, assignment_id)
            already_graded_user_ids = {
                sub.user_id for sub in subs if sub.score is not None
            }
        except Exception as fetch_err:
            logger.warning(
                "Could not check grades for assignment %d: %s",
                assignment_id,
                fetch_err,
            )

    to_grade: list[CompletenessReport] = []
    for report in complete_reports:
        if report.user_id in already_graded_user_ids:
            result.skipped_count += 1
            result.outcomes.append(
                AutoGradeOutcome(
                    user_id=report.user_id,
                    student_name=report.student_name,
                    status="skipped",
                )
            )
            logger.debug(
                "Skipping already-graded: user %d on assignment %d",
                report.user_id,
                assignment_id,
            )
        else:
            to_grade.append(report)

    grade_comment = f"Pending Review {assignment_name} 100%"
    total_to_grade = len(to_grade)

    for index, report in enumerate(to_grade, start=1):
        if on_progress is not None:
            on_progress(index, total_to_grade, report)

        if not execute and getattr(writer, "execute", False) is True:
            result.planned_count += 1
            result.outcomes.append(
                AutoGradeOutcome(
                    user_id=report.user_id,
                    student_name=report.student_name,
                    status="planned",
                    score=points_possible,
                    comment=grade_comment,
                )
            )
            continue

        try:
            write_outcome = writer.submit_grade(
                course_id,
                assignment_id,
                report.user_id,
                points_possible,
                comment=grade_comment,
            )
            backup_path = (
                str(write_outcome.backup_path)
                if isinstance(getattr(write_outcome, "backup_path", None), (Path, str))
                else None
            )
            if execute and write_outcome.executed:
                result.graded_count += 1
                result.outcomes.append(
                    AutoGradeOutcome(
                        user_id=report.user_id,
                        student_name=report.student_name,
                        status="graded",
                        score=points_possible,
                        comment=grade_comment,
                        backup_path=backup_path,
                    )
                )
            else:
                result.planned_count += 1
                result.outcomes.append(
                    AutoGradeOutcome(
                        user_id=report.user_id,
                        student_name=report.student_name,
                        status="planned",
                        score=points_possible,
                        comment=grade_comment,
                    )
                )
        except Exception as grade_error:
            logger.error(
                "Failed to grade user %d: %s",
                report.user_id,
                grade_error,
            )
            result.failed_count += 1
            error_msg = f"{report.student_name} (user {report.user_id}): {grade_error}"
            result.errors.append(error_msg)
            result.outcomes.append(
                AutoGradeOutcome(
                    user_id=report.user_id,
                    student_name=report.student_name,
                    status="failed",
                    score=points_possible,
                    comment=grade_comment,
                    error=str(grade_error),
                )
            )

    return result


def _delete_old_comments(
    canvas_client: Any,
    course_id: int,
    assignment_id: int,
    user_id: int,
    comment_ids: list[int],
) -> None:
    """Delete previous completeness comments, logging any failure."""
    for old_id in comment_ids:
        try:
            canvas_client.delete_submission_comment(
                course_id,
                assignment_id,
                user_id,
                old_id,
            )
        except Exception as del_err:
            logger.warning(
                "Could not delete comment %d: %s",
                old_id,
                del_err,
            )


def _post_comment_via_writer(
    writer: SafeCanvasWriter,
    course_id: int,
    assignment_id: int,
    report: CompletenessReport,
    comment_text: str,
    kind: str,
) -> tuple[bool, str | None, str | None]:
    """Post one comment through ``SafeCanvasWriter``, returning ``(ok, backup_path, err)``."""
    try:
        outcome = writer.post_submission_comment(
            course_id,
            assignment_id,
            report.user_id,
            comment_text,
        )
        backup_path = (
            str(outcome.backup_path)
            if isinstance(getattr(outcome, "backup_path", None), (Path, str))
            else None
        )
        return bool(outcome.executed), backup_path, None
    except Exception as comment_error:
        logger.error(
            "Failed to post %s comment for user %d: %s",
            kind,
            report.user_id,
            comment_error,
        )
        return False, None, f"{report.student_name} (user {report.user_id}) {kind}: {comment_error}"


def post_completeness_feedback(
    canvas_client: Any,
    writer: SafeCanvasWriter,
    course_id: int,
    assignment_id: int,
    assignment_name: str,
    reports: list[CompletenessReport],
    *,
    existing_comments: dict[tuple[int, int], dict[str, list[int]]] | None = None,
    templates_dir: Path | None = None,
    allow_generic_fallback: bool = False,
    execute: bool = False,
    on_progress: Callable[[int, int, CompletenessReport], None] | None = None,
) -> FeedbackPostResult:
    """Post two-part status + feedback comments for incomplete submissions."""
    incomplete_reports = [
        report
        for report in reports
        if report.user_id and report.verdict != CompletenessVerdict.COMPLETE
    ]

    used_generic_fallback = False
    if incomplete_reports and not templates_available(templates_dir):
        if not allow_generic_fallback:
            raise MissingTemplatesError(
                "Workspace feedback templates are missing; pass "
                "allow_generic_fallback=True or populate workspace templates/."
            )
        used_generic_fallback = True

    result = FeedbackPostResult(
        course_id=course_id,
        assignment_id=assignment_id,
        assignment_name=assignment_name,
        execute=execute,
        used_generic_fallback=used_generic_fallback,
    )

    if not incomplete_reports:
        return result

    if existing_comments is None:
        existing_comments = fetch_existing_completeness_comments(
            canvas_client,
            course_id,
            [assignment_id],
        )

    should_execute = execute and (getattr(writer, "execute", True) is not False)
    total = len(incomplete_reports)

    for index, report in enumerate(incomplete_reports, start=1):
        if on_progress is not None:
            on_progress(index, total, report)

        key = (assignment_id, report.user_id)
        buckets = existing_comments.get(key, {"status": [], "feedback": [], "legacy": []})
        old_status_ids = list(buckets.get("status", [])) + list(buckets.get("legacy", []))
        old_feedback_ids = list(buckets.get("feedback", []))
        all_old_ids = tuple(old_status_ids + old_feedback_ids)

        status_text = build_submission_status_comment(
            report,
            assignment_name=assignment_name,
        )
        feedback_text = build_submission_feedback_comment(
            report,
            assignment_name=assignment_name,
            templates_dir=templates_dir,
        )

        if not should_execute:
            planned_for_student = 1 + (1 if feedback_text is not None else 0)
            result.planned_count += planned_for_student
            if all_old_ids:
                result.would_replace_count += 1
            result.outcomes.append(
                FeedbackPostOutcome(
                    user_id=report.user_id,
                    student_name=report.student_name,
                    status="planned",
                    comments_planned=planned_for_student,
                    replaced_comment_ids=all_old_ids,
                    status_comment=status_text,
                    feedback_comment=feedback_text,
                )
            )
            continue

        if all_old_ids:
            result.replaced_count += 1

        student_posted = 0
        backup_paths: list[str] = []
        student_errors: list[str] = []

        _delete_old_comments(
            canvas_client,
            course_id,
            assignment_id,
            report.user_id,
            old_status_ids,
        )
        ok, backup_path, err = _post_comment_via_writer(
            writer,
            course_id,
            assignment_id,
            report,
            status_text,
            "status",
        )
        if ok:
            result.posted_count += 1
            student_posted += 1
            if backup_path:
                backup_paths.append(backup_path)
        else:
            result.failed_count += 1
            if err:
                student_errors.append(err)
                result.errors.append(err)

        _delete_old_comments(
            canvas_client,
            course_id,
            assignment_id,
            report.user_id,
            old_feedback_ids,
        )
        if feedback_text is not None:
            ok, backup_path, err = _post_comment_via_writer(
                writer,
                course_id,
                assignment_id,
                report,
                feedback_text,
                "feedback",
            )
            if ok:
                result.posted_count += 1
                student_posted += 1
                if backup_path:
                    backup_paths.append(backup_path)
            else:
                result.failed_count += 1
                if err:
                    student_errors.append(err)
                    result.errors.append(err)

        if not student_errors:
            student_status = "posted"
        elif student_posted > 0:
            student_status = "partial_failure"
        else:
            student_status = "failed"

        result.outcomes.append(
            FeedbackPostOutcome(
                user_id=report.user_id,
                student_name=report.student_name,
                status=student_status,
                comments_posted=student_posted,
                replaced_comment_ids=all_old_ids,
                status_comment=status_text,
                feedback_comment=feedback_text,
                backup_paths=tuple(backup_paths),
                errors=tuple(student_errors),
            )
        )

    return result
