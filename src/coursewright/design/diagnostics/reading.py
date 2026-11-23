"""The only door from this package to Canvas, and it is read-only.

``ReadOnlyCanvas`` names the handful of ``CanvasClient`` getters the
diagnostics may call.  Typing every function against this protocol means
a write method is not even visible to the code in this package, and
``tests/design/test_diagnostics_read_only.py`` proves no HTTP verb other
than GET ever leaves it.
"""

from __future__ import annotations

from typing import Any, Protocol

from coursewright.core.canvas.models import Assignment


class ReadOnlyCanvas(Protocol):
    """The subset of ``CanvasClient`` that Phase 1 is allowed to use."""

    base_url: str

    def get_courses(self, enrollment_type: str = "teacher") -> list[dict[str, Any]]: ...

    def get_course(self, course_id: int) -> dict[str, Any] | None: ...

    def get_modules(self, course_id: int) -> list[dict[str, Any]]: ...

    def get_module_items(self, course_id: int, module_id: int) -> list[dict[str, Any]]: ...

    def get_assignments(self, course_id: int) -> list[Assignment]: ...

    def get_assignment_groups(self, course_id: int) -> list[dict[str, Any]]: ...

    def get_quizzes(self, course_id: int) -> list[dict[str, Any]]: ...

    def get_discussion_topics(self, course_id: int) -> list[dict[str, Any]]: ...

    def get_announcements(self, course_id: int) -> list[dict[str, Any]]: ...

    def get_pages(self, course_id: int) -> list[dict[str, Any]]: ...

    def get_page(self, course_id: int, page_url: str) -> dict[str, Any] | None: ...

    def find_course_by_name(
        self, name_pattern: str, enrollment_type: str = "teacher"
    ) -> list[dict[str, Any]]: ...

    def _paginated_get(
        self, url: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...


def get_raw_assignments(canvas: ReadOnlyCanvas, course_id: int) -> list[dict[str, Any]]:
    """Return assignments as raw Canvas records (a GET, nothing else).

    ``CanvasClient.get_assignments`` returns typed ``Assignment`` models
    that drop ``external_tool_tag_attributes``, the field the LTI audit
    reads.  The source cantrip used the same raw list endpoint, so this
    helper issues one paginated GET to it.  It is the single place in the
    package that touches the client's paginated reader directly.
    """
    url = f"{canvas.base_url}/api/v1/courses/{course_id}/assignments"
    return canvas._paginated_get(url, {"per_page": 100})
