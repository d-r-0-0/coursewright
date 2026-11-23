"""Attendance status from Canvas activity over a lookback window.

Library code: it returns data and raises exceptions, it never prints
(AGENTS.md 3.8).  Progress is relayed through an optional callback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from coursewright.core.canvas.client import CanvasClient


@dataclass
class AttendanceStudentRecord:
    """Snapshot of one student's recent activity signals."""

    student_id: int
    student_name: str
    last_activity_at: datetime | None
    last_submission_at: datetime | None
    last_discussion_post_at: datetime | None
    attendance_status: str
    # Current overall course grade (0-100), populated from Canvas
    # enrollment grades when available.  None means Canvas returned
    # no grade data.
    current_score: float | None = None


@dataclass
class AttendanceReport:
    """Course attendance report for a specific lookback window."""

    lookback_days: int
    generated_at: datetime
    student_records: list[AttendanceStudentRecord]


def _parse_canvas_datetime(value: Any) -> datetime | None:
    """Parse Canvas datetime fields that are usually ISO-8601 strings."""
    if not value or not isinstance(value, str):
        return None
    normalized_value = value.replace("Z", "+00:00")
    try:
        parsed_value = datetime.fromisoformat(normalized_value)
    except ValueError:
        return None
    if parsed_value.tzinfo is None:
        return parsed_value.replace(tzinfo=UTC)
    return parsed_value


def _normalize_datetime(value: datetime | None) -> datetime | None:
    """Normalize naive datetimes to UTC-aware values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _latest_datetime(values: list[datetime | None]) -> datetime | None:
    """Return the latest datetime from non-null values."""
    valid_values = [value for value in values if value is not None]
    if not valid_values:
        return None
    return max(valid_values)


def _status_from_last_seen(
    last_seen_at: datetime | None,
    lookback_cutoff: datetime,
    warning_cutoff: datetime,
) -> str:
    """Compute the traffic-light attendance status.

    * **green** - last substantive activity is within the warning
      window (recent enough, no concern).
    * **yellow** - last activity is between the warning and lookback
      cutoffs (approaching the limit, a heads-up).
    * **red** - last activity is older than the lookback cutoff, or
      there is no recorded activity at all (needs immediate outreach
      under the institution's attendance policy).
    """
    if last_seen_at is None:
        return "red"
    if last_seen_at >= warning_cutoff:
        return "green"
    if last_seen_at >= lookback_cutoff:
        return "yellow"
    return "red"


def check_attendance(
    canvas_client: CanvasClient,
    course_id: int,
    lookback_days: int = 14,
    progress_callback: Callable[[str], None] | None = None,
) -> AttendanceReport:
    """Build a per-student attendance view from Canvas activity signals.

    Performance notes:
    * Submissions are fetched in ONE bulk call (``student_ids[]=all``)
      instead of one call per student.
    * Discussion entry scanning skips discussions whose ``posted_at``
      is older than the lookback window, avoiding stale-data fetches.

    Args:
        canvas_client: Authenticated Canvas client.
        course_id:     Canvas course ID to check.
        lookback_days: How many days back to consider "active".
        progress_callback: Optional callable receiving a short status
            string after each major step, so the UI can relay progress.
    """

    def _emit(message: str) -> None:
        if progress_callback:
            progress_callback(message)

    now_utc = datetime.now(UTC)
    lookback_cutoff = now_utc - timedelta(days=lookback_days)
    # Yellow warning zone: half the lookback window up to the full
    # window.  Anything more recent than half the window is green.
    warning_cutoff = now_utc - timedelta(days=lookback_days // 2)

    # -- Step 1: enrolled students ----------------------------------------
    _emit("Loading enrolled students…")
    enrolled_students = canvas_client.get_course_users(
        course_id=course_id,
        enrollment_type=["student"],
    )
    _emit(f"Found {len(enrolled_students)} students")

    # -- Step 2: bulk submissions (filtered to lookback window) ----------
    _emit(
        f"Loading submissions since "
        f"{lookback_cutoff.strftime('%Y-%m-%d')} "
        f"({lookback_days}-day window)…"
    )

    def _submission_page_progress(page_number: int, cumulative_count: int) -> None:
        """Relay per-page progress so the user sees continuous updates."""
        _emit(f"Submissions: page {page_number} ({cumulative_count} records)…")

    all_submissions = canvas_client.get_all_student_submissions(
        course_id,
        page_callback=_submission_page_progress,
        submitted_since=lookback_cutoff,
    )
    # Index: student_id -> latest submitted_at datetime
    latest_submission_by_student: dict[int, datetime] = {}
    for submission in all_submissions:
        submitted_at = _parse_canvas_datetime(submission.get("submitted_at"))
        if submitted_at is None:
            continue
        student_id = submission.get("user_id")
        if student_id is None:
            continue
        current_best = latest_submission_by_student.get(student_id)
        if current_best is None or submitted_at > current_best:
            latest_submission_by_student[student_id] = submitted_at
    _emit(
        f"Indexed {len(all_submissions)} recent submissions "
        f"across {len(latest_submission_by_student)} students"
    )

    # -- Step 3: discussion posts (skip old discussions) ------------------
    _emit("Scanning discussion posts…")
    all_discussions = canvas_client.get_discussions(course_id=course_id)
    most_recent_discussion_post_by_student: dict[int, datetime] = {}
    scanned_count = 0
    skipped_count = 0
    for discussion in all_discussions:
        # Skip discussions that predate the lookback window entirely:
        # no entries inside them can make a student "green".
        if discussion.posted_at and discussion.posted_at < lookback_cutoff:
            skipped_count += 1
            continue
        scanned_count += 1
        _emit(f"Scanning discussion {scanned_count}/{len(all_discussions)}…")
        discussion_entries = canvas_client.get_discussion_entries(
            course_id=course_id,
            topic_id=discussion.id,
            include_replies=True,
        )
        for discussion_entry in discussion_entries:
            parsed_entry_created_at = _normalize_datetime(discussion_entry.created_at)
            if parsed_entry_created_at is None:
                continue
            current_latest_post = most_recent_discussion_post_by_student.get(
                discussion_entry.user_id
            )
            if current_latest_post is None or parsed_entry_created_at > current_latest_post:
                most_recent_discussion_post_by_student[discussion_entry.user_id] = (
                    parsed_entry_created_at
                )
    _emit(f"Scanned {scanned_count} recent discussions, skipped {skipped_count} older ones")

    # -- Step 4: assemble per-student records -----------------------------
    _emit("Building attendance report…")
    student_records: list[AttendanceStudentRecord] = []
    for enrolled_student in enrolled_students:
        student_enrollment = enrolled_student.enrollments[0] if enrolled_student.enrollments else {}
        last_activity_at = _parse_canvas_datetime(student_enrollment.get("last_activity_at"))

        # Canvas returns grades inside the enrollment when
        # include[]=current_grades is present on the users request.
        # The shape is: enrollment["grades"]["current_score"].
        enrollment_grades = student_enrollment.get("grades") or {}
        raw_score = enrollment_grades.get("current_score")
        current_score: float | None = None
        if raw_score is not None:
            try:
                current_score = float(raw_score)
            except (TypeError, ValueError):
                current_score = None

        most_recent_submission_at = latest_submission_by_student.get(enrolled_student.id)
        most_recent_discussion_post_at = most_recent_discussion_post_by_student.get(
            enrolled_student.id
        )
        # Only submissions and discussion posts count toward
        # attendance.  Merely logging into the course is not enough:
        # a student must actively contribute to be "green".
        last_contribution_at = _latest_datetime(
            [most_recent_submission_at, most_recent_discussion_post_at]
        )
        attendance_status = _status_from_last_seen(
            last_seen_at=last_contribution_at,
            lookback_cutoff=lookback_cutoff,
            warning_cutoff=warning_cutoff,
        )

        student_records.append(
            AttendanceStudentRecord(
                student_id=enrolled_student.id,
                student_name=enrolled_student.name,
                last_activity_at=last_activity_at,
                last_submission_at=most_recent_submission_at,
                last_discussion_post_at=most_recent_discussion_post_at,
                attendance_status=attendance_status,
                current_score=current_score,
            )
        )

    _emit("Done!")
    return AttendanceReport(
        lookback_days=lookback_days,
        generated_at=now_utc,
        student_records=student_records,
    )
