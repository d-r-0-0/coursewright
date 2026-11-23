"""Inventories, diagnostics and reports, against recorded synthetic Canvas.

New tests (the source scripts had none) for ``diagnose_course``,
``list_courses``, ``list_modules``, ``list_module_items``,
``inspect_course``, ``scan_course``, ``find_courses`` and the report
writers.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from synthetic_canvas import COURSE_ID, SYNTHETIC_COURSE

from coursewright.design.diagnostics import diagnose, inventory, reports
from coursewright.design.diagnostics.models import (
    CourseNotFoundError,
    WorkspaceNotConfiguredError,
)

NOW = datetime(2026, 6, 1, 12, 0, 0)  # naive: treated as UTC


class TestCourseLookup:
    def test_list_courses_sorted_by_id(self, canvas) -> None:
        courses = inventory.list_courses(canvas)
        assert [course.id for course in courses] == [99, 101]
        assert courses[1].term_name == "Term One"

    def test_list_courses_filters_by_name_case_insensitively(self, canvas) -> None:
        assert [c.id for c in inventory.list_courses(canvas, "other")] == [99]

    def test_find_courses_matches_name_or_code(self, canvas) -> None:
        assert [c.id for c in inventory.find_courses(canvas, "syn-200")] == [99]
        assert {c.id for c in inventory.find_courses(canvas, "Synthetic")} == {99, 101}

    @pytest.mark.parametrize("pattern", ["", "x", "y" * 101])
    def test_find_courses_rejects_unusable_patterns(self, canvas, pattern) -> None:
        with pytest.raises(ValueError):
            inventory.find_courses(canvas, pattern)


class TestModuleInventories:
    def test_list_modules_reports_state_and_unlock_dates(self, canvas) -> None:
        modules = inventory.list_modules(canvas, COURSE_ID)
        assert [(m.id, m.published, m.unlock_at) for m in modules] == [
            (11, True, "2030-01-06T00:00:00Z"),
            (12, False, None),
        ]
        assert all(module.items == [] for module in modules)

    def test_list_modules_can_include_items(self, canvas) -> None:
        modules = inventory.list_modules(canvas, COURSE_ID, include_items=True)
        assert [len(module.items) for module in modules] == [3, 2]

    def test_missing_course_raises(self, canvas, recorded_canvas) -> None:
        recorded_canvas.routes.pop(f"/api/v1/courses/{COURSE_ID}")
        with pytest.raises(CourseNotFoundError):
            inventory.list_modules(canvas, COURSE_ID)

    def test_list_module_items_returns_everything_by_default(self, canvas) -> None:
        modules = inventory.list_module_items(canvas, COURSE_ID)
        assert [len(module.items) for module in modules] == [3, 2]

    def test_unpublished_filter_keeps_only_explicitly_unpublished(self, canvas) -> None:
        # Item 122 has no "published" key: the source treated that as published.
        modules = inventory.list_module_items(canvas, COURSE_ID, unpublished_only=True)
        assert [[item.id for item in module.items] for module in modules] == [[], [121]]

    def test_inspect_course_uses_embedded_items(self, canvas) -> None:
        inspection = inventory.inspect_course(canvas, COURSE_ID)
        assert inspection.name == SYNTHETIC_COURSE["name"]
        assert inspection.term_name == "Term One"
        assert inspection.workflow_state == "available"
        assert [len(module.items) for module in inspection.modules] == [1, 0]

    def test_inspect_missing_course_raises(self, canvas, recorded_canvas) -> None:
        recorded_canvas.routes.pop(f"/api/v1/courses/{COURSE_ID}")
        with pytest.raises(CourseNotFoundError):
            inventory.inspect_course(canvas, COURSE_ID)


class TestScanCourse:
    def test_counts_modules_and_items(self, canvas) -> None:
        scan = inventory.scan_course(canvas, COURSE_ID)
        assert (len(scan.modules), scan.total_items, scan.matches) == (2, 5, [])

    def test_matches_titles_case_insensitively_and_reports_only(
        self, canvas, recorded_canvas
    ) -> None:
        scan = inventory.scan_course(canvas, COURSE_ID, "live lab|welcome")
        assert [(m.module_id, m.item_id) for m in scan.matches] == [(11, 111), (12, 121)]
        assert recorded_canvas.methods_used == {"GET"}  # the source's --live delete is gone


class TestDiagnoseCourse:
    def test_counts_and_flags_for_the_recorded_course(self, canvas) -> None:
        result = diagnose.diagnose_course(canvas, SYNTHETIC_COURSE, now=NOW)
        assert result.course_id == COURSE_ID
        assert result.workflow_state == "available"
        assert (result.total_modules, result.total_items, result.total_assignments) == (2, 5, 3)
        assert result.unpublished_modules == ["Module Two"]
        assert result.modules_without_unlock_dates == ["Module Two"]
        assert result.missing_due_dates == ["Undated Reflection"]
        assert result.content_types == {"Page": 1, "ExternalTool": 3, "Discussion": 1}
        assert result.timestamp == "2026-06-01T12:00:00+00:00"

    def test_past_due_uses_aware_comparison(self, canvas) -> None:
        # The source compared aware vs naive datetimes, raised inside a bare
        # except and never reported anything.  This pins the fix.
        result = diagnose.diagnose_course(canvas, SYNTHETIC_COURSE, now=NOW)
        assert [(a.name, a.due_at) for a in result.past_due_dates] == [
            ("Lab With Due Date", "2020-01-15T23:59:00+00:00")
        ]

    def test_common_issue_checks(self, canvas) -> None:
        result = diagnose.diagnose_course(canvas, SYNTHETIC_COURSE, now=NOW)
        assert result.issues == [
            diagnose.ISSUE_UNPUBLISHED_MODULES,
            diagnose.ISSUE_MISSING_DUE_DATES,
            diagnose.ISSUE_FEW_MODULES,
        ]

    def test_empty_course_hits_every_threshold(self, canvas, recorded_canvas) -> None:
        recorded_canvas.routes["/api/v1/courses/101/modules"] = []
        recorded_canvas.routes["/api/v1/courses/101/assignments"] = []
        result = diagnose.diagnose_course(canvas, SYNTHETIC_COURSE, now=NOW)
        assert result.issues == [diagnose.ISSUE_FEW_MODULES, diagnose.ISSUE_NO_DISCUSSIONS]

    def test_mostly_unpublished_and_undated_are_flagged(self, canvas, recorded_canvas) -> None:
        modules = [{"id": n, "name": f"M{n}", "published": False} for n in range(1, 6)]
        recorded_canvas.routes["/api/v1/courses/101/modules"] = modules
        for module in modules:
            recorded_canvas.routes[f"/api/v1/courses/101/modules/{module['id']}/items"] = []
        recorded_canvas.routes["/api/v1/courses/101/assignments"] = [
            {"id": 1, "name": "A"},
            {"id": 2, "name": "B"},
        ]
        result = diagnose.diagnose_course(canvas, SYNTHETIC_COURSE, now=NOW)
        assert diagnose.ISSUE_MOSTLY_UNPUBLISHED in result.issues
        assert diagnose.ISSUE_MOSTLY_UNDATED in result.issues

    def test_by_id_fetches_the_course(self, canvas) -> None:
        result = diagnose.diagnose_course_by_id(canvas, COURSE_ID, now=NOW)
        assert result.course_name == "Synthetic Course A"

    def test_by_id_missing_course_raises(self, canvas, recorded_canvas) -> None:
        recorded_canvas.routes.pop(f"/api/v1/courses/{COURSE_ID}")
        with pytest.raises(CourseNotFoundError):
            diagnose.diagnose_course_by_id(canvas, COURSE_ID)


class TestReports:
    def test_diagnostics_report_lands_in_the_workspace(self, canvas, tmp_path) -> None:
        result = diagnose.diagnose_course(canvas, SYNTHETIC_COURSE, now=NOW)
        path = reports.save_diagnostics_report(result, tmp_path, now=NOW)
        assert path.parent == tmp_path / "reports" / "diagnostics"
        assert path.name == "diagnostics_101_20260601_120000.json"
        assert json.loads(path.read_text())["course_name"] == "Synthetic Course A"

    def test_context_report_round_trips(self, canvas, tmp_path) -> None:
        from coursewright.design.diagnostics.text_contexts import audit_text_contexts

        report = audit_text_contexts(canvas, COURSE_ID)
        path = reports.save_context_audit_report(report, tmp_path, now=NOW)
        assert path.name == "text_contexts_101_20260601_120000.json"
        assert len(json.loads(path.read_text())["publisher_contexts"]) == 2

    def test_no_workspace_is_an_error_not_a_repo_write(self, canvas, monkeypatch) -> None:
        monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
        monkeypatch.setattr(reports, "resolve_workspace_directory", lambda: None)
        result = diagnose.diagnose_course(canvas, SYNTHETIC_COURSE, now=NOW)
        with pytest.raises(WorkspaceNotConfiguredError):
            reports.save_diagnostics_report(result, None, now=NOW)

    def test_environment_workspace_is_honoured(self, canvas, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
        result = diagnose.diagnose_course(canvas, SYNTHETIC_COURSE, now=NOW)
        path = reports.save_diagnostics_report(result, now=NOW)
        assert tmp_path in path.parents

    def test_an_existing_report_is_never_overwritten(self, canvas, tmp_path) -> None:
        result = diagnose.diagnose_course(canvas, SYNTHETIC_COURSE, now=NOW)
        reports.save_diagnostics_report(result, tmp_path, now=NOW)
        with pytest.raises(FileExistsError):
            reports.save_diagnostics_report(result, tmp_path, now=NOW)
