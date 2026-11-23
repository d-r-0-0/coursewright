"""Tests for attendance status, warning rendering and inactivity scan.

The source file also held Textual screen tests (AttendanceScreen,
WarningPreviewScreen).  Those belong to ``ui/`` and are not ported
here; the library helpers they exercised are tested below.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from coursewright.core.canvas.safe_write import SafeWriteError
from coursewright.ta.attendance.outreach import (
    _determine_last_activity_description,
    find_students_inactive_for_threshold,
    load_attendance_warning_template,
    render_warning_for_student,
    send_canvas_conversation_message,
)
from coursewright.ta.attendance.status import (
    AttendanceReport,
    AttendanceStudentRecord,
    _latest_datetime,
    _normalize_datetime,
    _parse_canvas_datetime,
    _status_from_last_seen,
    check_attendance,
)
from coursewright.ta.attendance.templates import (
    OUTREACH_TEMPLATES,
    OutreachContact,
)

SYNTHETIC_CONTACT = OutreachContact(
    instructor_signature="Prof. Example",
    instructor_email="instructor@example.test",
    office_hours_url="https://example.test/office-hours",
    institution_name="Example College",
    attendance_policy_name="Example College Attendance Policy",
    support_services_url="https://example.test/help",
    lab_example_url="https://example.test/lab-example",
)


class TestAttendanceDateHelpers:
    """Unit tests for private date parsing/normalization helper functions."""

    def test_parse_canvas_datetime_valid_iso(self) -> None:
        parsed_value = _parse_canvas_datetime("2026-03-01T13:45:22+00:00")
        assert parsed_value is not None
        assert parsed_value.tzinfo is not None
        assert parsed_value.year == 2026

    def test_parse_canvas_datetime_z_suffix(self) -> None:
        parsed_value = _parse_canvas_datetime("2026-03-01T13:45:22Z")
        assert parsed_value is not None
        assert parsed_value.tzinfo is not None
        assert parsed_value.utcoffset() == timedelta(0)

    @pytest.mark.parametrize("bad_value", [None, "not-a-date", 42])
    def test_parse_canvas_datetime_invalid_inputs(self, bad_value) -> None:
        assert _parse_canvas_datetime(bad_value) is None

    def test_normalize_datetime_naive_to_utc(self) -> None:
        naive_value = datetime(2026, 1, 2, 3, 4, 5)
        normalized_value = _normalize_datetime(naive_value)
        assert normalized_value is not None
        assert normalized_value.tzinfo is UTC

    def test_normalize_datetime_aware_unchanged(self) -> None:
        aware_value = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        normalized_value = _normalize_datetime(aware_value)
        assert normalized_value == aware_value

    def test_normalize_datetime_none(self) -> None:
        assert _normalize_datetime(None) is None

    def test_latest_datetime_empty_values(self) -> None:
        assert _latest_datetime([]) is None

    def test_latest_datetime_mixed_values(self) -> None:
        early = datetime(2026, 1, 1, tzinfo=UTC)
        late = datetime(2026, 1, 5, tzinfo=UTC)
        assert _latest_datetime([None, early, None, late]) == late

    def test_status_from_last_seen_green(self) -> None:
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=14)
        warning_cutoff = now - timedelta(days=7)
        recent_seen = now - timedelta(days=1)
        assert _status_from_last_seen(recent_seen, cutoff, warning_cutoff) == "green"

    def test_status_from_last_seen_yellow(self) -> None:
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=14)
        warning_cutoff = now - timedelta(days=7)
        # 10 days ago is between warning (7) and lookback (14) cutoffs
        borderline_seen = now - timedelta(days=10)
        assert _status_from_last_seen(borderline_seen, cutoff, warning_cutoff) == "yellow"

    def test_status_from_last_seen_red_past_cutoff(self) -> None:
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=14)
        warning_cutoff = now - timedelta(days=7)
        old_seen = now - timedelta(days=30)
        assert _status_from_last_seen(old_seen, cutoff, warning_cutoff) == "red"

    def test_status_from_last_seen_red_no_activity(self) -> None:
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=14)
        warning_cutoff = now - timedelta(days=7)
        assert _status_from_last_seen(None, cutoff, warning_cutoff) == "red"


class TestCheckAttendance:
    """Integration-style unit tests for check_attendance with mocked Canvas."""

    def test_check_attendance_builds_expected_report(self) -> None:
        now_utc = datetime.now(UTC)
        mock_client = MagicMock()

        mock_client.get_course_users.return_value = [
            SimpleNamespace(
                id=1,
                name="Alice Student",
                enrollments=[{"last_activity_at": (now_utc - timedelta(days=1)).isoformat()}],
            ),
            SimpleNamespace(
                id=2,
                name="Bob Learner",
                enrollments=[{"last_activity_at": None}],
            ),
            SimpleNamespace(
                id=3,
                name="Charlie Newbie",
                enrollments=[{"last_activity_at": None}],
            ),
            SimpleNamespace(
                id=4,
                name="Dana Borderline",
                enrollments=[{"last_activity_at": None}],
            ),
        ]

        mock_client.get_all_student_submissions.return_value = [
            {"user_id": 1, "submitted_at": (now_utc - timedelta(days=2)).isoformat()},
            {"user_id": 2, "submitted_at": (now_utc - timedelta(days=40)).isoformat()},
            {"user_id": 2, "submitted_at": (now_utc - timedelta(days=30)).isoformat()},
            {"user_id": 4, "submitted_at": (now_utc - timedelta(days=10)).isoformat()},
            {"user_id": 999, "submitted_at": None},
        ]

        old_discussion = SimpleNamespace(id=99, posted_at=now_utc - timedelta(days=45))
        recent_discussion = SimpleNamespace(id=100, posted_at=now_utc - timedelta(days=2))
        mock_client.get_discussions.return_value = [old_discussion, recent_discussion]
        mock_client.get_discussion_entries.return_value = [
            SimpleNamespace(user_id=2, created_at=now_utc - timedelta(days=25)),
        ]

        progress_messages: list[str] = []

        report = check_attendance(
            canvas_client=mock_client,
            course_id=42,
            lookback_days=14,
            progress_callback=progress_messages.append,
        )

        assert isinstance(report, AttendanceReport)
        assert report.lookback_days == 14
        assert len(report.student_records) == 4

        status_by_student = {
            record.student_id: record.attendance_status for record in report.student_records
        }
        assert status_by_student[1] == "green"  # submission 2 days ago
        assert status_by_student[2] == "red"  # latest was 25 days ago (past cutoff)
        assert status_by_student[3] == "red"  # no activity at all
        assert status_by_student[4] == "yellow"  # submission 10 days ago (7-14 day zone)

        mock_client.get_discussion_entries.assert_called_once_with(
            course_id=42,
            topic_id=100,
            include_replies=True,
        )

        # Verify submitted_since is passed for efficiency filtering
        call_kwargs = mock_client.get_all_student_submissions.call_args
        assert call_kwargs is not None
        assert "submitted_since" in call_kwargs.kwargs or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] is not None
        ), "submitted_since should be passed to limit API volume"

        assert any("Loading enrolled students" in message for message in progress_messages)
        assert progress_messages[-1] == "Done!"

    def test_current_score_is_read_from_enrollment_grades(self) -> None:
        mock_client = MagicMock()
        mock_client.get_course_users.return_value = [
            SimpleNamespace(
                id=1,
                name="Alice Student",
                enrollments=[{"grades": {"current_score": "87.5"}}],
            ),
            SimpleNamespace(
                id=2,
                name="Bob Learner",
                enrollments=[{"grades": {"current_score": "oops"}}],
            ),
        ]
        mock_client.get_all_student_submissions.return_value = []
        mock_client.get_discussions.return_value = []
        report = check_attendance(canvas_client=mock_client, course_id=1)
        scores = {record.student_id: record.current_score for record in report.student_records}
        assert scores == {1: 87.5, 2: None}


# =================================================================
# Warning template helper tests
# =================================================================


class TestWarningTemplateHelpers:
    """Tests for the warning template rendering helpers."""

    def test_determine_last_activity_submission_only(self) -> None:
        """When only a submission exists, use that for the description."""
        now_utc = datetime.now(UTC)
        record = AttendanceStudentRecord(
            student_id=10,
            student_name="Test Student",
            last_activity_at=now_utc - timedelta(days=5),
            last_submission_at=now_utc - timedelta(days=5),
            last_discussion_post_at=None,
            attendance_status="yellow",
        )
        date_str, description, days = _determine_last_activity_description(record)
        assert "Assignment submission" in description
        assert date_str != "N/A"
        assert days == 5

    def test_determine_last_activity_post_only(self) -> None:
        """When only a discussion post exists, use that."""
        now_utc = datetime.now(UTC)
        record = AttendanceStudentRecord(
            student_id=10,
            student_name="Test Student",
            last_activity_at=now_utc - timedelta(days=5),
            last_submission_at=None,
            last_discussion_post_at=now_utc - timedelta(days=5),
            attendance_status="yellow",
        )
        date_str, description, days = _determine_last_activity_description(record)
        assert "Discussion post" in description
        assert date_str != "N/A"
        assert days == 5

    def test_determine_last_activity_no_activity(self) -> None:
        """When neither signal exists, return empty date and descriptive message."""
        record = AttendanceStudentRecord(
            student_id=10,
            student_name="Test Student",
            last_activity_at=None,
            last_submission_at=None,
            last_discussion_post_at=None,
            attendance_status="red",
        )
        date_str, description, days = _determine_last_activity_description(record)
        assert date_str == ""
        assert "don't see any submission" in description
        assert days == -1

    def test_determine_last_activity_picks_most_recent(self) -> None:
        """When both signals exist, pick the more recent one."""
        now_utc = datetime.now(UTC)
        record = AttendanceStudentRecord(
            student_id=10,
            student_name="Test Student",
            last_activity_at=now_utc - timedelta(days=3),
            last_submission_at=now_utc - timedelta(days=10),
            last_discussion_post_at=now_utc - timedelta(days=3),
            attendance_status="yellow",
        )
        _date_str, description, _days = _determine_last_activity_description(record)
        assert "Discussion post" in description

    def test_render_warning_for_student_fills_placeholders(self) -> None:
        """The rendered template should contain the student and course names."""
        now_utc = datetime.now(UTC)
        record = AttendanceStudentRecord(
            student_id=10,
            student_name="Test Student",
            last_activity_at=now_utc - timedelta(days=20),
            last_submission_at=now_utc - timedelta(days=20),
            last_discussion_post_at=None,
            attendance_status="yellow",
        )
        template = (
            "Subject: Warning for {course_name}\n"
            "\n"
            "Dear {student_first_name}, absent {days_absent}. "
            "{activity_line}. "
            "Deadline: {deadline_date}."
        )
        subject, body = render_warning_for_student(
            student_record=record,
            course_name="COURSE_A",
            template_text=template,
            contact=SYNTHETIC_CONTACT,
        )
        assert subject == "Warning for COURSE_A"
        assert "Test" in body  # first name
        assert "more than 14 days" in body
        assert "Assignment submission" in body

    def test_builtin_template_is_filled_from_contact(self) -> None:
        """No placeholder is left over and contact details come from config."""
        record = AttendanceStudentRecord(
            student_id=10,
            student_name="Test Student",
            last_activity_at=None,
            last_submission_at=None,
            last_discussion_post_at=None,
            attendance_status="red",
        )
        subject, body = render_warning_for_student(
            student_record=record,
            course_name="COURSE_A",
            template_text=load_attendance_warning_template(),
            contact=SYNTHETIC_CONTACT,
        )
        assert subject == "Important: Attendance Warning for COURSE_A"
        for expected in (
            "instructor@example.test",
            "https://example.test/office-hours",
            "Example College Attendance Policy",
            "Prof. Example",
        ):
            assert expected in body
        assert "{" not in body and "}" not in body

    def test_load_template_from_path(self, tmp_path) -> None:
        template_file = tmp_path / "custom.md"
        template_file.write_text("Subject: Hi\n\nBody", encoding="utf-8")
        assert load_attendance_warning_template(template_file) == "Subject: Hi\n\nBody"


class TestOutreachTemplates:
    """Every template formats cleanly from synthetic configuration."""

    def test_all_templates_fill_without_leftover_placeholders(self) -> None:
        values = {
            "course_name": "COURSE_A",
            "student_first_name": "Test",
            "student_name": "Test Student",
            "activity_line": "Activity",
            "days_absent": "3 days",
            "deadline_date": "January 01, 2030",
            "assignment_name": "Lab 1",
            **SYNTHETIC_CONTACT.as_placeholders(),
        }
        for name, template in OUTREACH_TEMPLATES.items():
            filled = template.format(**values)
            assert "{" not in filled, name

    def test_templates_carry_no_institution_specific_values(self) -> None:
        for name, template in OUTREACH_TEMPLATES.items():
            assert "@" not in template.replace("{instructor_email}", ""), name
            assert "http" not in template.replace("{support_services_url}", "").replace(
                "{office_hours_url}", ""
            ).replace("{lab_example_url}", ""), name


# =================================================================
# Inactivity scan and sending
# =================================================================


class TestInactivityScanAndSend:
    def test_find_students_inactive_for_threshold(self) -> None:
        now_utc = datetime.now(UTC)
        client = MagicMock()
        client.base_url = "https://canvas.example.test"
        client.get_course_users.return_value = [
            SimpleNamespace(id=1, name="Zed Active"),
            SimpleNamespace(id=2, name="Amy Stale"),
            SimpleNamespace(id=3, name="Bo Never"),
        ]
        client.get_all_student_submissions.return_value = [
            {
                "user_id": 1,
                "submitted_at": (now_utc - timedelta(days=1)).isoformat(),
                "assignment": {"name": "Lab 1"},
            },
            {
                "user_id": 2,
                "submitted_at": (now_utc - timedelta(days=30)).isoformat(),
                "assignment": {"name": "Lab 0"},
            },
        ]
        client._paginated_get.return_value = []
        client.get_discussions.return_value = []

        flagged = find_students_inactive_for_threshold(client, course_id=5)

        assert [student.student_name for student in flagged] == ["Amy Stale", "Bo Never"]
        assert flagged[0].last_activity_description == "Submission: Lab 0"
        assert flagged[1].last_activity_at is None

    def test_send_message_dry_run_by_default(self) -> None:
        """Default (execute=False) must not post to Canvas and needs no workspace."""
        monkeypatched = self._isolated_client()
        success, detail = send_canvas_conversation_message(
            monkeypatched, 7, "Subj", "Body", course_id=5
        )
        assert success is True
        assert "Planned (dry run)" in detail
        monkeypatched.post_conversation_message.assert_not_called()
        monkeypatched._make_request.assert_not_called()

    @staticmethod
    def _isolated_client() -> MagicMock:
        client = MagicMock()
        client.base_url = "https://canvas.example.test"
        return client

    def test_send_message_execute_backs_up_first(self, monkeypatch, tmp_path) -> None:
        """execute=True must write a timestamped JSON backup before the POST."""
        monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
        monkeypatch.chdir(tmp_path)
        client = MagicMock()
        client.base_url = "https://canvas.example.test"
        client.post_conversation_message.return_value = {"id": 1}

        backup_dir = tmp_path / "workspace" / "backups"
        success, detail = send_canvas_conversation_message(
            client, 7, "Subj", "Body", course_id=5, execute=True, backup_directory=backup_dir
        )
        assert success is True
        assert detail == ""

        # The timestamped backup was written before the POST.
        backup_files = list(backup_dir.glob("*.json"))
        assert len(backup_files) == 1
        record = json.loads(backup_files[0].read_text())
        assert record["operation"] == "attendance-outreach-send"
        assert record["payload"]["context_code"] == "course_5"
        assert record["payload"]["recipients"] == ["7"]
        assert record["payload"]["force_new"] is True

        # And the Canvas POST actually went through the safe client method.
        client.post_conversation_message.assert_called_once_with(5, 7, "Subj", "Body")

    def test_send_message_execute_failure_reports_status(self, monkeypatch, tmp_path) -> None:
        """execute=True surfaces the Canvas error text when the post fails."""
        monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
        monkeypatch.chdir(tmp_path)
        client = MagicMock()
        client.base_url = "https://canvas.example.test"
        client.post_conversation_message.return_value = None

        success, detail = send_canvas_conversation_message(
            client, 7, "Subj", "Body", execute=True, backup_directory=tmp_path / "backups"
        )
        assert success is False
        assert "No response" in detail

    def test_send_message_execute_needs_a_workspace(self, monkeypatch, tmp_path) -> None:
        """execute=True with no workspace raises SafeWriteError and posts nothing."""
        monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
        monkeypatch.chdir(tmp_path)
        client = MagicMock()
        client.base_url = "https://canvas.example.test"
        with pytest.raises(SafeWriteError):
            send_canvas_conversation_message(client, 7, "Subj", "Body", execute=True)
        client.post_conversation_message.assert_not_called()
        client._make_request.assert_not_called()
