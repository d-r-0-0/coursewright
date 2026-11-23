"""Tests for the safe apply step and Canvas helpers of the MindTap backfill."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from coursewright.core.canvas.safe_write import BackupFailedError, SafeWriteError
from coursewright.ta.gradeliner import mindtap as backfill


def _fill_change() -> backfill.GradeChange:
    return backfill.GradeChange(
        mindtap_name="Quiz One",
        mindtap_raw=90.0,
        mindtap_max=100.0,
        canvas_assignment_id=10,
        canvas_assignment_name="Quiz One",
        canvas_points_possible=20.0,
        match_ratio=1.0,
        new_score=18.0,
        current_score=None,
        action="FILL",
    )


def test_apply_grade_changes_is_a_dry_run_by_default():
    canvas = MagicMock()
    result = backfill.apply_grade_changes(canvas, 1, 2, [_fill_change()])
    assert result.executed is False
    assert result.planned == 1
    canvas.submit_grade.assert_not_called()


def test_apply_grade_changes_backs_up_then_writes(tmp_path):
    canvas = MagicMock()
    canvas.submit_grade.return_value = True
    skipped = _fill_change()
    skipped.action = "SKIP-HAS-GRADE"

    result = backfill.apply_grade_changes(
        canvas, 1, 2, [_fill_change(), skipped], execute=True, backup_directory=tmp_path
    )

    assert result.executed is True
    assert result.written == 1
    assert result.backup_path is not None
    assert result.backup_path.exists()
    canvas.submit_grade.assert_called_once()
    assert canvas.submit_grade.call_args.kwargs["score"] == 18.0


def test_apply_grade_changes_reports_refused_writes(tmp_path):
    canvas = MagicMock()
    canvas.submit_grade.return_value = False
    result = backfill.apply_grade_changes(
        canvas, 1, 2, [_fill_change()], execute=True, backup_directory=tmp_path
    )
    assert result.written == 0
    assert result.failed_assignment_names == ["Quiz One"]


def test_apply_grade_changes_aborts_when_backup_fails(tmp_path):
    canvas = MagicMock()
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("file in the way")
    with pytest.raises(BackupFailedError):
        backfill.apply_grade_changes(
            canvas, 1, 2, [_fill_change()], execute=True, backup_directory=blocked / "sub"
        )
    canvas.submit_grade.assert_not_called()


def test_apply_grade_changes_needs_a_workspace_to_execute(monkeypatch, tmp_path):
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    canvas = MagicMock()
    with pytest.raises(SafeWriteError):
        backfill.apply_grade_changes(canvas, 1, 2, [_fill_change()], execute=True)
    canvas.submit_grade.assert_not_called()


def test_discover_latest_gradebook_csv_uses_inbox_newest_match(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "unrelated.csv").write_text("x")
    wanted = downloads / "MindTap_Course_Gradebook_2026.csv"
    wanted.write_text("x")

    candidate = backfill.discover_latest_gradebook_csv(
        download_directory=downloads, workspace_import_directory=tmp_path / "none"
    )
    assert candidate is not None
    assert candidate.path == wanted

    nothing = backfill.discover_latest_gradebook_csv(
        download_directory=tmp_path / "empty", workspace_import_directory=tmp_path / "none"
    )
    assert nothing is None


def test_find_canvas_student_and_load_current_scores():
    canvas = MagicMock()
    student = MagicMock(id=1)
    student.name = "Alex Example"
    canvas.get_course_users.return_value = [student]
    assert backfill.find_canvas_student(canvas, 5, "Example, Alex").id == 1
    assert backfill.find_canvas_student(canvas, 5, "Nobody") is None

    canvas.get_student_submissions.return_value = [
        {"assignment_id": 10, "score": 4.0},
        {"assignment_id": 11, "score": None},
        {"score": 1.0},
    ]
    assert backfill.load_current_scores(canvas, 5, 1) == {10: 4.0, 11: None}
