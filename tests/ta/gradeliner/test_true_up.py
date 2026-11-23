"""Tests for the read-only true-up preview and the safe grade submit."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from coursewright.core.canvas.models import Submission
from coursewright.core.canvas.safe_write import SafeWriteError
from coursewright.ta.gradeliner.models import GradeImportRow
from coursewright.ta.gradeliner.orchestrator import GradeLinerUpper
from coursewright.ta.gradeliner.true_up import TrueUpError, build_true_up_preview


def _write_caches(export_root, course_id=123):
    folder = export_root / "COURSE-101"
    folder.mkdir(parents=True)
    (folder / "gradeliner_assignment_map.json").write_text(
        json.dumps(
            {
                "course_id": course_id,
                "canvas_course_name": "Test Course",
                "mappings": [
                    {
                        "netacad_column": "Exam A",
                        "canvas_assignment_name": "Canvas Exam A",
                        "canvas_assignment_id": 2001,
                        "canvas_points_possible": 100.0,
                        "netacad_points_possible": 100.0,
                    }
                ],
            }
        )
    )
    students = [
        ("Alpha Stu", "alpha@example.edu", 601),
        ("Beta Stu", "beta@example.edu", 602),
        ("Gamma Stu", "gamma@example.edu", 603),
        ("Absent Stu", "absent@example.edu", 604),
    ]
    (folder / "gradeliner_student_map.json").write_text(
        json.dumps(
            {
                "course_id": course_id,
                "students": [
                    {
                        "netacad_name": name,
                        "netacad_email": email,
                        "canvas_user_id": user_id,
                        "canvas_name": name,
                        "canvas_login_id": name.split()[0].lower(),
                    }
                    for name, email, user_id in students
                ],
            }
        )
    )


@pytest.fixture
def canvas_client():
    client = MagicMock()
    client.get_course.return_value = {"id": 123, "name": "Test", "course_code": "COURSE-101"}
    client.get_assignment_submissions.return_value = [
        Submission(id=1, user_id=601, workflow_state="graded", score=50.0),
        Submission(id=2, user_id=602, workflow_state="graded", score=90.0),
        Submission(id=3, user_id=603, workflow_state="graded", score=70.0),
    ]
    return client


def _netacad_csv(tmp_path):
    path = tmp_path / "netacad.csv"
    path.write_text(
        "NAME,EMAIL,Exam A\n"
        "Alpha Stu,alpha@example.edu,80\n"
        "Beta Stu,beta@example.edu,60\n"
        "Gamma Stu,gamma@example.edu,70\n"
        "Other Section,other@example.edu,99\n"
    )
    return path


def test_true_up_preview_groups_rows_and_never_writes(tmp_path, canvas_client):
    _write_caches(tmp_path / "exports")
    preview = build_true_up_preview(
        canvas_client, 123, _netacad_csv(tmp_path), export_root=tmp_path / "exports"
    )

    assert [row.student_name for row in preview.increases] == ["Alpha Stu"]
    assert [row.student_name for row in preview.decreases] == ["Beta Stu"]
    assert [row.student_name for row in preview.matches] == ["Gamma Stu"]
    assert preview.unmatched_netacad_names == ["Other Section"]
    assert preview.matched_student_count == 3
    assert preview.mapped_students_missing_from_export == ["Absent Stu"]
    canvas_client.submit_grade.assert_not_called()


def test_true_up_preview_refuses_a_cache_for_another_course(tmp_path, canvas_client):
    _write_caches(tmp_path / "exports", course_id=999)
    with pytest.raises(TrueUpError, match="different course"):
        build_true_up_preview(
            canvas_client, 123, _netacad_csv(tmp_path), export_root=tmp_path / "exports"
        )


def test_true_up_preview_needs_cached_mappings(tmp_path, canvas_client):
    with pytest.raises(TrueUpError, match="No cached"):
        build_true_up_preview(
            canvas_client, 123, _netacad_csv(tmp_path), export_root=tmp_path / "exports"
        )


def _rows() -> list[GradeImportRow]:
    return [
        GradeImportRow(
            canvas_user_id=601,
            canvas_name="Alpha Stu",
            assignment_id=2001,
            assignment_name="Canvas Exam A",
            netacad_score=80.0,
            scaled_score=80.0,
        )
    ]


def _liner(tmp_path, canvas_client):
    csv_path = _netacad_csv(tmp_path)
    return GradeLinerUpper(canvas_client, None, 123, csv_path, export_root=tmp_path / "exports")


def test_submit_grades_is_a_dry_run_by_default(tmp_path, canvas_client):
    result = _liner(tmp_path, canvas_client).submit_grades(_rows())
    assert result == {"successful": 0, "failed": 0, "planned": 1}
    canvas_client.submit_grade.assert_not_called()


def test_submit_grades_backs_up_then_writes(tmp_path, canvas_client):
    canvas_client.submit_grade.return_value = True
    canvas_client.get_submission.return_value = {"score": 70, "grade": "70"}
    progress = MagicMock()
    result = _liner(tmp_path, canvas_client).submit_grades(
        _rows(), progress, execute=True, backup_directory=tmp_path / "backups"
    )
    assert result["successful"] == 1
    assert len(list((tmp_path / "backups").glob("*.json"))) == 1
    backup = json.loads(next((tmp_path / "backups").glob("*.json")).read_text())
    assert backup["state_before"] == {"score": 70, "grade": "70"}
    canvas_client.get_submission.assert_called_once_with(123, 2001, 601)
    progress.assert_called_once_with(1, 1, "Alpha Stu")


def test_submit_grades_without_workspace_refuses_to_execute(tmp_path, canvas_client, monkeypatch):
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    liner = _liner(tmp_path, canvas_client)
    with pytest.raises(SafeWriteError):
        liner.submit_grades(_rows(), execute=True)
    canvas_client.submit_grade.assert_not_called()


def test_grade_import_stops_when_current_grades_cannot_be_read(tmp_path, canvas_client):
    canvas_client.get_assignment_submissions.side_effect = RuntimeError("Offline")
    with pytest.raises(SafeWriteError, match="check current grades"):
        _liner(tmp_path, canvas_client)._fetch_already_graded_pairs({2001})
    canvas_client.submit_grade.assert_not_called()


def test_grade_import_preserves_excused_submissions(tmp_path, canvas_client):
    canvas_client.get_assignment_submissions.return_value = [
        Submission(id=1, user_id=601, excused=True, score=None, grade="EX"),
    ]
    assert _liner(tmp_path, canvas_client)._fetch_already_graded_pairs({2001}) == {(2001, 601)}


def test_submit_grades_refuses_missing_snapshot(tmp_path, canvas_client):
    canvas_client.get_submission.return_value = None
    with pytest.raises(SafeWriteError, match="current state"):
        _liner(tmp_path, canvas_client).submit_grades(
            _rows(), execute=True, backup_directory=tmp_path / "backups"
        )
    canvas_client.submit_grade.assert_not_called()


def test_submit_grades_surfaces_canvas_refusal(tmp_path, canvas_client):
    canvas_client.get_submission.return_value = {"score": 70}
    canvas_client.submit_grade.return_value = False
    with pytest.raises(SafeWriteError, match="did not accept"):
        _liner(tmp_path, canvas_client).submit_grades(
            _rows(), execute=True, backup_directory=tmp_path / "backups"
        )


def test_zero_ungraded_preserves_excused_submissions(tmp_path, canvas_client):
    from coursewright.core.canvas.safe_write import SafeCanvasWriter
    from coursewright.ta.gradeliner.orchestrator import zero_ungraded_submissions

    canvas_client.get_assignment_submissions.return_value = [
        Submission(id=1, user_id=601, excused=True, score=None, grade="EX"),
        Submission(id=2, user_id=602, score=None),
    ]
    writer = SafeCanvasWriter(canvas_client, tmp_path, execute=False)
    assert zero_ungraded_submissions(canvas_client, writer, 123, [2001]) == (1, 1)
    canvas_client.submit_grade.assert_not_called()
