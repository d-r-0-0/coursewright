"""Unit tests for the breadcrumb header widget."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader


class _DummyCourse:
    def __init__(self, course_code: str, name: str) -> None:
        self.course_code = course_code
        self.name = name


class BreadcrumbHeaderTestApp(App):
    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state

    def compose(self) -> ComposeResult:
        yield BreadcrumbHeader()


@pytest.mark.asyncio
async def test_breadcrumb_header_renders_course_code_and_name() -> None:
    app_state = AppState()
    app_state.current_course = _DummyCourse(
        course_code="ABC111",
        name="Intro to Networking",
    )
    app = BreadcrumbHeaderTestApp(app_state)
    async with app.run_test():
        breadcrumb = app.query_one(BreadcrumbHeader)
        assert str(breadcrumb.render()) == "ABC111 · Intro to Networking"
