"""Breadcrumb header widget for course-context Textual screens."""

from __future__ import annotations

from textual.widgets import Static


class BreadcrumbHeader(Static):
    """Render the currently-selected course as a dim one-line breadcrumb.

    The widget reads ``app.app_state.current_course`` when mounted.
    If no course is selected, it intentionally renders a blank line so
    every drill-down screen still reserves one row of vertical space.
    """

    DEFAULT_CSS = """
    BreadcrumbHeader {
        width: 100%;
        height: 1;
        color: $text-muted;
    }
    """

    def on_mount(self) -> None:
        """Populate the breadcrumb text after the widget is attached."""
        self.update(self._build_breadcrumb_text())

    def _build_breadcrumb_text(self) -> str:
        """Build dim breadcrumb text from ``app.app_state.current_course``."""
        app_state = getattr(self.app, "app_state", None)
        current_course = getattr(app_state, "current_course", None)
        if current_course is None:
            return " "

        course_code = getattr(current_course, "course_code", "") or ""
        course_name = getattr(current_course, "name", "") or ""

        if course_code and course_name:
            return f"[dim]{course_code} · {course_name}[/dim]"
        if course_name:
            return f"[dim]{course_name}[/dim]"
        if course_code:
            return f"[dim]{course_code}[/dim]"
        return " "
