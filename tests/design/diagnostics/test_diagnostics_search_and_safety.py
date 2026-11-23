"""Course text search, page reading, and the read-only guarantee.

New tests for ``collect_surfaces``, ``search_course``, ``read_pages`` and
for acceptance items 2 and 5: nothing in this package imports a UI
library, and nothing in it can write to Canvas.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from synthetic_canvas import COURSE_ID

import coursewright.design.diagnostics as diagnostics_package
from coursewright.design.diagnostics import (
    audit_text_contexts,
    diagnose_course_by_id,
    inspect_course,
    list_courses,
    list_module_items,
    list_modules,
    read_pages,
    scan_course,
    search,
    search_course,
)
from coursewright.design.diagnostics.models import TextSurface

PACKAGE_DIRECTORY = Path(diagnostics_package.__file__).parent


class TestSurfaces:
    def test_every_surface_kind_is_collected(self, canvas) -> None:
        locations = [surface.location for surface in search.collect_surfaces(canvas, COURSE_ID)]
        assert "Syllabus" in locations
        assert "Module item [Module One] Chapter Reading" in locations
        assert "Page: Course Policies" in locations
        assert "Assignment: Lab With Due Date" in locations
        assert "Quiz: Quiz One" in locations
        assert "Discussion: Week 2 Discussion" in locations
        assert "Announcement: Hello" in locations


class TestSearch:
    def test_finds_stale_prose_everywhere_it_hides(self, canvas) -> None:
        result = search_course(canvas, COURSE_ID, "room 12", context_characters=5)
        assert result.course_name == "Synthetic Course A"
        assert {m.location for m in result.matches} == {
            "Syllabus",
            "Assignment: Lab With Due Date",
            "Quiz: Quiz One",
        }
        assert result.match_count == 3

    def test_default_search_ignores_markup(self, canvas) -> None:
        assert search_course(canvas, COURSE_ID, "video.example.com").match_count == 0

    def test_markup_search_finds_hrefs(self, canvas) -> None:
        result = search_course(canvas, COURSE_ID, "video.example.com", search_markup=True)
        assert [m.location for m in result.matches] == ["Page: Course Policies"]

    def test_module_item_urls_are_searched(self, canvas) -> None:
        result = search_course(canvas, COURSE_ID, r"LTI\.aspx")
        assert [m.location for m in result.matches] == [
            "Module item [Module One] Lecture Recording"
        ]

    def test_snippet_is_trimmed_to_the_context_window(self) -> None:
        surface = TextSurface(location="Here", text="<p>aaaa NEEDLE bbbb</p>")
        (match,) = search.search_surfaces([surface], "needle", context_characters=2)
        assert match.snippet == "...a NEEDLE b..."

    def test_empty_surfaces_are_skipped(self) -> None:
        assert search.search_surfaces([TextSurface(location="x", text="")], ".") == []


class TestReadPages:
    def test_title_fragment_matches_any_case(self, canvas) -> None:
        pages = read_pages(canvas, COURSE_ID, "POLICIES")
        assert [page.url for page in pages] == ["course-policies"]

    def test_text_keeps_block_breaks_and_drops_tags(self, canvas) -> None:
        (page,) = read_pages(canvas, COURSE_ID, "policies")
        assert page.text == "Policies\nSubmit by Friday.\nvideo"
        assert "<a href" in page.html

    def test_no_match_returns_nothing(self, canvas) -> None:
        assert read_pages(canvas, COURSE_ID, "no such page") == []


class TestReadOnly:
    """Acceptance 5: no function in this package can write to Canvas."""

    def test_every_public_function_issues_only_get_requests(
        self, canvas, recorded_canvas, tmp_path
    ) -> None:
        list_courses(canvas)
        list_modules(canvas, COURSE_ID, include_items=True)
        list_module_items(canvas, COURSE_ID)
        inspect_course(canvas, COURSE_ID)
        scan_course(canvas, COURSE_ID, "lab")
        diagnose_course_by_id(canvas, COURSE_ID)
        search_course(canvas, COURSE_ID, "room")
        read_pages(canvas, COURSE_ID, "late")
        audit_text_contexts(canvas, COURSE_ID)
        assert recorded_canvas.requests_seen  # something really ran
        assert recorded_canvas.methods_used == {"GET"}

    def test_source_calls_no_write_method(self) -> None:
        forbidden = re.compile(
            r"^(create|update|delete|post|put|reorder|move|submit|upload|copy|set|zero)_"
            r"|^_send_|^_make_request$|^writer$|SafeCanvasWriter"
        )
        offenders: list[str] = []
        for source_file in PACKAGE_DIRECTORY.glob("*.py"):
            for node in ast.walk(ast.parse(source_file.read_text())):
                name = None
                if isinstance(node, ast.Attribute):
                    name = node.attr
                elif isinstance(node, ast.Name):
                    name = node.id
                if name and forbidden.search(name):
                    offenders.append(f"{source_file.name}:{node.lineno} {name}")
        assert offenders == []

    def test_no_ui_library_is_imported(self) -> None:
        banned = {"rich", "typer", "textual"}
        offenders: list[str] = []
        for source_file in PACKAGE_DIRECTORY.glob("*.py"):
            for node in ast.walk(ast.parse(source_file.read_text())):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    if module.split(".")[0] in banned or module.startswith("coursewright.ui"):
                        offenders.append(f"{source_file.name}: {module}")
        assert offenders == []

    def test_library_never_prints(self) -> None:
        for source_file in PACKAGE_DIRECTORY.glob("*.py"):
            calls = [
                node
                for node in ast.walk(ast.parse(source_file.read_text()))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"print", "input"}
            ]
            assert calls == [], source_file.name
