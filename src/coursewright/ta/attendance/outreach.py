"""Attendance warning workflow for inactivity outreach.

Finds students whose last submission, quiz attempt or discussion post
is older than a threshold, renders the warning message for a student,
and sends it through Canvas Conversations.

Library code (AGENTS.md 3.8): it returns data.  Previewing the flagged
students as a table is the job of ``ui/``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.models import CanvasUser
from coursewright.core.canvas.safe_write import (
    SafeWriteError,
    default_backup_directory,
    write_backup,
)
from coursewright.core.config.manager import resolve_workspace_directory
from coursewright.ta.attendance.status import AttendanceStudentRecord, _parse_canvas_datetime
from coursewright.ta.attendance.templates import ATTENDANCE_WARNING_TEMPLATE, OutreachContact

logger = logging.getLogger(__name__)


@dataclass
class LastActivitySignal:
    """One student activity signal pulled from Canvas."""

    occurred_at: datetime
    description: str


@dataclass
class FlaggedInactiveStudent:
    """Student who crossed the inactivity threshold."""

    student_id: int
    student_name: str
    last_activity_at: datetime | None
    last_activity_description: str


def _choose_latest_signal(signals: list[LastActivitySignal]) -> LastActivitySignal | None:
    """Return the most recent signal from a list, or None if empty."""
    if not signals:
        return None
    return max(signals, key=lambda signal: signal.occurred_at)


def _collect_latest_submission_signal_by_student(
    canvas_client: CanvasClient,
    course_id: int,
) -> dict[int, LastActivitySignal]:
    """Pull assignment submission activity for all students in one pass."""
    raw_submissions = canvas_client.get_all_student_submissions(course_id=course_id)
    latest_submission_signal_by_student: dict[int, LastActivitySignal] = {}

    for submission in raw_submissions:
        student_id = submission.get("user_id")
        submitted_at = _parse_canvas_datetime(submission.get("submitted_at"))
        if student_id is None or submitted_at is None:
            continue
        assignment_name = submission.get("assignment", {}).get("name", "Assignment submission")
        candidate_signal = LastActivitySignal(
            occurred_at=submitted_at,
            description=f"Submission: {assignment_name}",
        )
        existing_signal = latest_submission_signal_by_student.get(student_id)
        if existing_signal is None or candidate_signal.occurred_at > existing_signal.occurred_at:
            latest_submission_signal_by_student[student_id] = candidate_signal

    return latest_submission_signal_by_student


def _collect_latest_quiz_signal_by_student(
    canvas_client: CanvasClient,
    course_id: int,
) -> dict[int, LastActivitySignal]:
    """Pull quiz/test attempt activity for all students."""
    quizzes_url = f"{canvas_client.base_url}/api/v1/courses/{course_id}/quizzes"
    raw_quizzes = canvas_client._paginated_get(quizzes_url, params={"per_page": 100})
    latest_quiz_signal_by_student: dict[int, LastActivitySignal] = {}

    for raw_quiz in raw_quizzes:
        quiz_id = raw_quiz.get("id")
        if quiz_id is None:
            continue
        quiz_title = raw_quiz.get("title", "Quiz")
        quiz_submissions_url = (
            f"{canvas_client.base_url}/api/v1/courses/{course_id}/quizzes/{quiz_id}/submissions"
        )
        response = canvas_client._make_request(
            "GET", quiz_submissions_url, params={"per_page": 100}
        )
        if response is None or response.status_code != 200:
            continue
        raw_submission_wrapper = response.json()
        raw_quiz_submissions = raw_submission_wrapper.get("quiz_submissions", [])
        for raw_quiz_submission in raw_quiz_submissions:
            student_id = raw_quiz_submission.get("user_id")
            finished_at = _parse_canvas_datetime(raw_quiz_submission.get("finished_at"))
            if student_id is None or finished_at is None:
                continue
            candidate_signal = LastActivitySignal(
                occurred_at=finished_at,
                description=f"Quiz attempt: {quiz_title}",
            )
            existing_signal = latest_quiz_signal_by_student.get(student_id)
            if (
                existing_signal is None
                or candidate_signal.occurred_at > existing_signal.occurred_at
            ):
                latest_quiz_signal_by_student[student_id] = candidate_signal

    return latest_quiz_signal_by_student


def _collect_latest_discussion_signal_by_student(
    canvas_client: CanvasClient,
    course_id: int,
) -> dict[int, LastActivitySignal]:
    """Pull latest discussion-post activity by student."""
    latest_discussion_signal_by_student: dict[int, LastActivitySignal] = {}
    all_discussions = canvas_client.get_discussions(course_id=course_id)

    for discussion in all_discussions:
        discussion_entries = canvas_client.get_discussion_entries(
            course_id=course_id,
            topic_id=discussion.id,
            include_replies=True,
        )
        for discussion_entry in discussion_entries:
            if discussion_entry.created_at is None:
                continue
            created_at_value = discussion_entry.created_at
            if created_at_value.tzinfo is None:
                created_at_value = created_at_value.replace(tzinfo=UTC)
            candidate_signal = LastActivitySignal(
                occurred_at=created_at_value,
                description=f"Discussion post: {discussion.title}",
            )
            existing_signal = latest_discussion_signal_by_student.get(discussion_entry.user_id)
            if (
                existing_signal is None
                or candidate_signal.occurred_at > existing_signal.occurred_at
            ):
                latest_discussion_signal_by_student[discussion_entry.user_id] = candidate_signal

    return latest_discussion_signal_by_student


def find_students_inactive_for_threshold(
    canvas_client: CanvasClient,
    course_id: int,
    inactivity_days_threshold: int = 14,
) -> list[FlaggedInactiveStudent]:
    """Build the at-risk student list based on latest known activity."""
    cutoff_datetime = datetime.now(UTC) - timedelta(days=inactivity_days_threshold)
    enrolled_students: list[CanvasUser] = canvas_client.get_course_users(
        course_id=course_id,
        enrollment_type=["student"],
    )

    latest_submission_signal_by_student = _collect_latest_submission_signal_by_student(
        canvas_client=canvas_client,
        course_id=course_id,
    )
    latest_quiz_signal_by_student = _collect_latest_quiz_signal_by_student(
        canvas_client=canvas_client,
        course_id=course_id,
    )
    latest_discussion_signal_by_student = _collect_latest_discussion_signal_by_student(
        canvas_client=canvas_client,
        course_id=course_id,
    )

    flagged_inactive_students: list[FlaggedInactiveStudent] = []
    for student in enrolled_students:
        most_recent_signal = _choose_latest_signal(
            [
                signal
                for signal in [
                    latest_submission_signal_by_student.get(student.id),
                    latest_quiz_signal_by_student.get(student.id),
                    latest_discussion_signal_by_student.get(student.id),
                ]
                if signal is not None
            ]
        )

        if most_recent_signal is None:
            flagged_inactive_students.append(
                FlaggedInactiveStudent(
                    student_id=student.id,
                    student_name=student.name,
                    last_activity_at=None,
                    last_activity_description="No submission, quiz, or discussion activity",
                )
            )
            continue

        if most_recent_signal.occurred_at <= cutoff_datetime:
            flagged_inactive_students.append(
                FlaggedInactiveStudent(
                    student_id=student.id,
                    student_name=student.name,
                    last_activity_at=most_recent_signal.occurred_at,
                    last_activity_description=most_recent_signal.description,
                )
            )

    flagged_inactive_students.sort(key=lambda student: student.student_name.lower())
    return flagged_inactive_students


def load_attendance_warning_template(template_path: Path | None = None) -> str:
    """Return the attendance warning template text.

    With no path the built-in template is returned; pass a path to
    use an instructor-edited copy from the private workspace.
    """
    if template_path is None:
        return ATTENDANCE_WARNING_TEMPLATE
    return template_path.read_text(encoding="utf-8")


def _determine_last_activity_description(
    student_record: AttendanceStudentRecord | FlaggedInactiveStudent,
) -> tuple[str, str, int]:
    """Derive the last activity date string, description, and days since.

    Returns ``(formatted_date, description, days_absent)`` based on
    whichever signal, submission or discussion post, is more recent.
    """
    candidates: list[tuple[datetime, str]] = []
    for attribute, description in (
        ("last_submission_at", "Assignment submission"),
        ("last_discussion_post_at", "Discussion post"),
        ("last_activity_at", getattr(student_record, "last_activity_description", "Activity")),
    ):
        activity_at = getattr(student_record, attribute, None)
        if activity_at is not None:
            candidates.append((activity_at, description))

    if not candidates:
        return ("", "I don't see any submission or discussion activity on record", -1)

    latest_datetime, description = max(candidates, key=lambda candidate: candidate[0])
    if latest_datetime.tzinfo is None:
        latest_datetime = latest_datetime.replace(tzinfo=UTC)
    days_absent = (datetime.now(UTC) - latest_datetime).days
    return (latest_datetime.strftime("%Y-%m-%d"), description, days_absent)


def render_warning_for_student(
    student_record: AttendanceStudentRecord | FlaggedInactiveStudent,
    course_name: str,
    template_text: str,
    contact: OutreachContact,
    *,
    deadline_days: int = 7,
) -> tuple[str, str]:
    """Fill the warning template and return ``(subject_line, body)``.

    The first line of the template is ``Subject: ...``; the body
    starts at line 3 (after the blank separator line).  Instructor
    and institution details come from *contact*.
    """
    last_activity_date, last_activity_description, days_absent = (
        _determine_last_activity_description(student_record)
    )
    deadline_date = (datetime.now(UTC) + timedelta(days=deadline_days)).strftime("%B %d, %Y")

    # Use just the first name for a warmer, more personal tone.
    student_first_name = next(iter(student_record.student_name.split()), "Student")

    # Build human-friendly strings for edge cases.
    if days_absent < 0:
        # No activity on record at all.
        days_absent_text = "more than 14 days"
        activity_line = last_activity_description
    elif days_absent > 14:
        days_absent_text = "more than 14 days"
        activity_line = (
            f"The last activity I have on record for you was on "
            f"{last_activity_date} ({last_activity_description})"
        )
    else:
        days_absent_text = f"{days_absent} days"
        activity_line = (
            f"The last activity I have on record for you was on "
            f"{last_activity_date} ({last_activity_description})"
        )

    filled_template = template_text.format(
        course_name=course_name,
        student_name=student_record.student_name,
        student_first_name=student_first_name,
        activity_line=activity_line,
        days_absent=days_absent_text,
        deadline_date=deadline_date,
        last_activity_date=last_activity_date or "No recorded activity",
        last_activity_description=last_activity_description,
        **contact.as_placeholders(),
    )

    # First line is "Subject: ...", body starts after blank line.
    lines = filled_template.splitlines()
    if not lines or not lines[0].startswith("Subject:"):
        return ("Attendance Warning", filled_template.strip())
    subject_line = lines[0].removeprefix("Subject:").strip()
    # Body is everything after the first blank separator line.
    message_body = "\n".join(lines[2:]).strip()
    return (subject_line, message_body)


def send_canvas_conversation_message(
    canvas_client: CanvasClient,
    student_id: int,
    subject_line: str,
    message_body: str,
    course_id: int | None = None,
    *,
    execute: bool = False,
    backup_directory: Path | None = None,
) -> tuple[bool, str]:
    """Send one attendance warning message using Canvas Conversations.

    Dry run by default (AGENTS.md 3.11): with ``execute=False`` nothing is
    posted to Canvas and no workspace is required — the caller gets back a
    preview of what would be sent.  With ``execute=True`` a timestamped JSON
    backup is written first (the write path shared with every other ta
    Canvas write); if no private workspace is configured a
    :class:`~coursewright.core.canvas.safe_write.SafeWriteError` is raised
    and nothing is posted.

    Returns a ``(success, detail)`` tuple.  On failure, *detail* contains
    the Canvas error text so callers can surface it.
    """
    payload: dict[str, Any] = {
        "recipients": [str(student_id)],
        "subject": subject_line,
        "body": message_body,
        "force_new": True,
    }
    # Canvas often 500s without a course context; provide it when known.
    if course_id is not None:
        payload["context_code"] = f"course_{course_id}"

    # Dry run by default: preview only, no Canvas POST, no workspace needed.
    if not execute:
        return (
            True,
            f"Planned (dry run): conversation to student {student_id}"
            + (f" in course {course_id}" if course_id is not None else ""),
        )

    # Execute: a timestamped JSON backup must exist before the POST lands.
    if backup_directory is None:
        workspace_directory = resolve_workspace_directory()
        if workspace_directory is None:
            raise SafeWriteError(
                "No private workspace is configured; set COURSEWRIGHT_WORKSPACE "
                "so the attendance-outreach backup has somewhere to go."
            )
        backup_directory = default_backup_directory(workspace_directory)
    write_backup(
        backup_directory,
        "attendance-outreach-send",
        f"conversation to student {student_id}"
        + (f" (course {course_id})" if course_id is not None else ""),
        payload,
        None,
    )
    result = canvas_client.post_conversation_message(
        course_id, student_id, subject_line, message_body
    )
    if result is None or result is False:
        return (False, "No response from Canvas (network error or timeout)")
    return (True, "")
