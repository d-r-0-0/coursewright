"""Unit tests for ``coursewright.ta.packettracer.orchestrator``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from coursewright.core.canvas.models import (
    Assignment,
    Submission,
    SubmissionAttachment,
    SubmissionComment,
)
from coursewright.core.canvas.safe_write import (
    SafeCanvasWriter,
    default_backup_directory,
)
from coursewright.ta.packettracer import (
    REQUIRED_FEEDBACK_TEMPLATES,
    CompletenessReport,
    CompletenessVerdict,
    MissingTemplatesError,
    auto_grade_complete_submissions,
    check_packet_tracer_assignment,
    fetch_existing_completeness_comments,
    post_completeness_feedback,
    templates_available,
)
from coursewright.ta.packettracer.classifier import (
    COMPLETENESS_COMMENT_MARKER,
    COMPLETENESS_FEEDBACK_MARKER,
    COMPLETENESS_STATUS_MARKER,
)


def _complete_submission(
    user_id: int = 1,
    name: str = "Student One",
    score: float | None = None,
) -> Submission:
    return Submission(
        id=user_id * 100,
        user_id=user_id,
        submitted_at="2025-01-15T12:00:00Z",
        workflow_state="submitted",
        score=score,
        attachments=[
            SubmissionAttachment(id=1, display_name="Lab_Report.pdf", filename="Lab_Report.pdf"),
            SubmissionAttachment(id=2, display_name="network.pka", filename="network.pka"),
        ],
        user={"name": name},
    )


def _incomplete_submission(
    user_id: int = 2,
    name: str = "Student Two",
) -> Submission:
    return Submission(
        id=user_id * 100,
        user_id=user_id,
        submitted_at="2025-01-15T12:00:00Z",
        workflow_state="submitted",
        attachments=[
            SubmissionAttachment(id=3, display_name="Lab_Report.pdf", filename="Lab_Report.pdf"),
        ],
        user={"name": name},
    )


def _unsubmitted_submission(
    user_id: int = 3,
    name: str = "Student Three",
) -> Submission:
    return Submission(
        id=user_id * 100,
        user_id=user_id,
        submitted_at=None,
        workflow_state="unsubmitted",
        attachments=[],
        user={"name": name},
    )


def _populate_templates(templates_dir: Path) -> Path:
    templates_dir.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FEEDBACK_TEMPLATES:
        (templates_dir / name).write_text(
            f"Guidance for {{student_name}} on {{assignment_name}} ({name}).",
            encoding="utf-8",
        )
    return templates_dir


class TestTemplatesAvailable:
    def test_false_when_unset_and_no_dir(self, monkeypatch) -> None:
        monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
        assert templates_available() is False

    def test_false_when_dir_missing_or_whitespace_only(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        assert templates_available(missing) is False

        empty_dir = tmp_path / "templates"
        empty_dir.mkdir()
        (empty_dir / "missing_submission.md").write_text("   \n\t ", encoding="utf-8")
        (empty_dir / "unrelated.md").write_text("hello", encoding="utf-8")
        assert templates_available(empty_dir) is False

    @pytest.mark.parametrize("template_name", REQUIRED_FEEDBACK_TEMPLATES)
    def test_true_when_any_required_template_has_content(
        self, tmp_path: Path, template_name: str
    ) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / template_name).write_text("Please resubmit.", encoding="utf-8")
        assert templates_available(templates_dir) is True

    def test_uses_workspace_environment_variable_when_dir_none(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "missing_packet_tracer.md").write_text("Add .pka", encoding="utf-8")
        monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
        assert templates_available() is True


class TestCheckPacketTracerAssignment:
    def test_fetches_assignment_and_computes_counts_filtering_test_user(
        self, tmp_path: Path
    ) -> None:
        templates_dir = _populate_templates(tmp_path / "templates")
        client = MagicMock()
        client.get_assignments.return_value = [
            Assignment(id=10, name="11.2.1 Packet Tracer Lab", points_possible=50.0),
            Assignment(id=20, name="Other Lab", points_possible=25.0),
        ]
        client.get_assignment_submissions.return_value = [
            _complete_submission(user_id=0, name="Test Student"),
            _complete_submission(user_id=1, name="Student One"),
            _incomplete_submission(user_id=2, name="Student Two"),
            _unsubmitted_submission(user_id=3, name="Student Three"),
            Submission(
                id=400,
                user_id=4,
                submitted_at="2025-01-15T12:00:00Z",
                workflow_state="submitted",
                attachments=[
                    SubmissionAttachment(id=4, display_name="notes.txt", filename="notes.txt")
                ],
                user={"name": "Student Four"},
            ),
        ]

        summary = check_packet_tracer_assignment(
            client,
            42,
            10,
            templates_dir=templates_dir,
        )

        assert summary.course_id == 42
        assert summary.assignment_id == 10
        assert summary.assignment_name == "11.2.1 Packet Tracer Lab"
        assert summary.points_possible == 50.0
        assert len(summary.reports) == 4
        assert all(r.user_id != 0 for r in summary.reports)
        assert summary.complete_count == 1
        assert summary.incomplete_count == 3
        assert summary.partial_count == 1
        assert summary.not_submitted_count == 1
        assert summary.no_lab_files_count == 1
        assert summary.templates_found is True

    def test_uses_passed_assignment_without_fetching_all_assignments(self) -> None:
        client = MagicMock()
        client.get_assignment_submissions.return_value = [_complete_submission(user_id=1)]
        assignment = Assignment(id=10, name="Passed Lab", points_possible=30.0)

        summary = check_packet_tracer_assignment(
            client,
            42,
            10,
            assignment=assignment,
            templates_dir=Path("/nonexistent"),
        )

        client.get_assignments.assert_not_called()
        assert summary.assignment_name == "Passed Lab"
        assert summary.points_possible == 30.0
        assert summary.templates_found is False


class TestFetchExistingCompletenessComments:
    def test_groups_status_feedback_and_legacy_comments_and_survives_errors(self) -> None:
        client = MagicMock()

        def _get_comments(_course_id: int, assignment_id: int):
            if assignment_id == 2:
                raise RuntimeError("Canvas timeout")
            return [
                Submission(
                    id=100,
                    user_id=7,
                    submission_comments=[
                        SubmissionComment(id=10, comment=f"{COMPLETENESS_STATUS_MARKER} x"),
                        SubmissionComment(id=11, comment=f"{COMPLETENESS_FEEDBACK_MARKER} y"),
                        SubmissionComment(id=12, comment=f"{COMPLETENESS_COMMENT_MARKER} z"),
                        SubmissionComment(id=13, comment="Regular instructor comment"),
                    ],
                )
            ]

        client.get_submissions_with_comments.side_effect = _get_comments

        existing = fetch_existing_completeness_comments(client, 42, [1, 2])
        assert existing == {
            (1, 7): {"status": [10], "feedback": [11], "legacy": [12]},
        }


class TestAutoGradeCompleteSubmissions:
    def test_dry_run_plans_grades_without_canvas_writes(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.get_assignment_submissions.return_value = [
            _complete_submission(user_id=1, score=None),
            _complete_submission(user_id=2, score=50.0),
        ]
        writer = SafeCanvasWriter(client, default_backup_directory(tmp_path), execute=False)
        reports = [
            CompletenessReport("Student One", 1, CompletenessVerdict.COMPLETE),
            CompletenessReport("Student Two", 2, CompletenessVerdict.COMPLETE),
            CompletenessReport("Student Three", 3, CompletenessVerdict.PARTIAL),
            CompletenessReport("Test Student", 0, CompletenessVerdict.COMPLETE),
        ]

        result = auto_grade_complete_submissions(
            client,
            writer,
            42,
            10,
            "Lab 1",
            50.0,
            reports,
            execute=False,
        )

        assert result.execute is False
        assert result.planned_count == 1
        assert result.graded_count == 0
        assert result.skipped_count == 1
        assert result.failed_count == 0
        client.submit_grade.assert_not_called()
        assert list(default_backup_directory(tmp_path).glob("*.json")) == []
        assert [o.status for o in result.outcomes] == ["skipped", "planned"]

    def test_execute_writes_backups_grades_and_skips_graded(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.get_submission.return_value = {"id": 100, "score": None}
        client.submit_grade.return_value = True
        writer = SafeCanvasWriter(client, default_backup_directory(tmp_path), execute=True)
        reports = [
            CompletenessReport("Student One", 1, CompletenessVerdict.COMPLETE),
            CompletenessReport("Student Two", 2, CompletenessVerdict.COMPLETE),
        ]
        progress_events: list[tuple[int, int, int]] = []

        result = auto_grade_complete_submissions(
            client,
            writer,
            42,
            10,
            "Lab 1",
            50.0,
            reports,
            already_graded_user_ids={2},
            execute=True,
            on_progress=lambda cur, tot, rep: progress_events.append((cur, tot, rep.user_id)),
        )

        client.get_assignment_submissions.assert_not_called()
        assert result.graded_count == 1
        assert result.skipped_count == 1
        assert result.failed_count == 0
        assert progress_events == [(1, 1, 1)]
        client.submit_grade.assert_called_once_with(
            42,
            10,
            1,
            50.0,
            "Pending Review Lab 1 100%",
        )
        assert len(list(default_backup_directory(tmp_path).glob("*_submit_grade.json"))) == 1

    def test_records_errors_without_stopping_remaining_students(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.get_submission.side_effect = [
            {"id": 100, "score": None},
            {"id": 200, "score": None},
        ]
        client.submit_grade.side_effect = [False, True]
        writer = SafeCanvasWriter(client, default_backup_directory(tmp_path), execute=True)
        reports = [
            CompletenessReport("Student One", 1, CompletenessVerdict.COMPLETE),
            CompletenessReport("Student Two", 2, CompletenessVerdict.COMPLETE),
        ]

        result = auto_grade_complete_submissions(
            client,
            writer,
            42,
            10,
            "Lab 1",
            100.0,
            reports,
            already_graded_user_ids=set(),
            execute=True,
        )

        assert result.failed_count == 1
        assert result.graded_count == 1
        assert len(result.errors) == 1
        assert "Student One" in result.errors[0]
        assert [o.status for o in result.outcomes] == ["failed", "graded"]


class TestPostCompletenessFeedback:
    def test_missing_templates_raises_when_fallback_disallowed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
        client = MagicMock()
        writer = SafeCanvasWriter(client, default_backup_directory(tmp_path), execute=False)
        reports = [
            CompletenessReport(
                "Student Two",
                2,
                CompletenessVerdict.PARTIAL,
                has_document=True,
                document_files=["Lab.pdf"],
                missing_types=["Packet Tracer file (.pkt or .pka)"],
            )
        ]

        with pytest.raises(MissingTemplatesError):
            post_completeness_feedback(
                client,
                writer,
                42,
                10,
                "Lab 1",
                reports,
                templates_dir=tmp_path / "empty_templates",
                allow_generic_fallback=False,
                execute=False,
            )

        client.get_submissions_with_comments.assert_not_called()
        client.post_submission_comment.assert_not_called()

    def test_complete_only_reports_do_not_require_templates(self, tmp_path: Path) -> None:
        client = MagicMock()
        writer = SafeCanvasWriter(client, default_backup_directory(tmp_path), execute=False)
        reports = [CompletenessReport("Student One", 1, CompletenessVerdict.COMPLETE)]

        result = post_completeness_feedback(
            client,
            writer,
            42,
            10,
            "Lab 1",
            reports,
            templates_dir=tmp_path / "empty_templates",
            allow_generic_fallback=False,
            execute=False,
        )

        assert result.posted_count == 0
        assert result.planned_count == 0
        assert result.used_generic_fallback is False

    def test_dry_run_never_deletes_or_posts_and_tracks_planned(self, tmp_path: Path) -> None:
        templates_dir = _populate_templates(tmp_path / "templates")
        client = MagicMock()
        client.get_submissions_with_comments.return_value = [
            Submission(
                id=200,
                user_id=2,
                submission_comments=[
                    SubmissionComment(id=10, comment=f"{COMPLETENESS_STATUS_MARKER} old"),
                ],
            )
        ]
        writer = SafeCanvasWriter(client, default_backup_directory(tmp_path), execute=False)
        reports = [
            CompletenessReport(
                "Student Two",
                2,
                CompletenessVerdict.PARTIAL,
                has_document=True,
                document_files=["Lab.pdf"],
                missing_types=["Packet Tracer file (.pkt or .pka)"],
            )
        ]

        result = post_completeness_feedback(
            client,
            writer,
            42,
            10,
            "Lab 1",
            reports,
            templates_dir=templates_dir,
            execute=False,
        )

        assert result.execute is False
        assert result.posted_count == 0
        assert result.replaced_count == 0
        assert result.planned_count == 2
        assert result.would_replace_count == 1
        assert result.used_generic_fallback is False
        client.delete_submission_comment.assert_not_called()
        client.post_submission_comment.assert_not_called()
        assert list(default_backup_directory(tmp_path).glob("*.json")) == []

    def test_execute_with_fallback_deletes_old_and_posts_new(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.get_submissions_with_comments.return_value = [
            Submission(
                id=200,
                user_id=2,
                submission_comments=[
                    SubmissionComment(id=10, comment=f"{COMPLETENESS_STATUS_MARKER} old"),
                    SubmissionComment(id=11, comment=f"{COMPLETENESS_FEEDBACK_MARKER} old"),
                    SubmissionComment(id=12, comment=f"{COMPLETENESS_COMMENT_MARKER} legacy"),
                ],
            )
        ]
        client.get_submission.return_value = {"id": 200, "score": None}
        client.delete_submission_comment.return_value = True
        client.post_submission_comment.return_value = True
        writer = SafeCanvasWriter(client, default_backup_directory(tmp_path), execute=True)
        reports = [
            CompletenessReport(
                "Student Two",
                2,
                CompletenessVerdict.PARTIAL,
                has_document=True,
                document_files=["Lab.pdf"],
                missing_types=["Packet Tracer file (.pkt or .pka)"],
            )
        ]

        result = post_completeness_feedback(
            client,
            writer,
            42,
            10,
            "Lab 1",
            reports,
            templates_dir=tmp_path / "missing_templates",
            allow_generic_fallback=True,
            execute=True,
        )

        assert result.used_generic_fallback is True
        assert result.posted_count == 2
        assert result.replaced_count == 1
        assert result.failed_count == 0
        deleted_ids = [c.args[3] for c in client.delete_submission_comment.call_args_list]
        assert sorted(deleted_ids) == [10, 11, 12]
        assert client.post_submission_comment.call_count == 2
        backups = list(default_backup_directory(tmp_path).glob("*_post_submission_comment.json"))
        assert len(backups) == 2

    def test_execute_catches_comment_write_failure_cleanly(self, tmp_path: Path) -> None:
        templates_dir = _populate_templates(tmp_path / "templates")
        client = MagicMock()
        client.get_submissions_with_comments.return_value = []
        client.get_submission.return_value = {"id": 200, "score": None}
        client.post_submission_comment.side_effect = [False, True]
        writer = SafeCanvasWriter(client, default_backup_directory(tmp_path), execute=True)
        reports = [
            CompletenessReport(
                "Student Two",
                2,
                CompletenessVerdict.PARTIAL,
                has_document=True,
                document_files=["Lab.pdf"],
                missing_types=["Packet Tracer file (.pkt or .pka)"],
            )
        ]

        result = post_completeness_feedback(
            client,
            writer,
            42,
            10,
            "Lab 1",
            reports,
            templates_dir=templates_dir,
            execute=True,
        )

        assert result.failed_count == 1
        assert result.posted_count == 1
        assert len(result.errors) == 1
        assert result.outcomes[0].status == "partial_failure"
