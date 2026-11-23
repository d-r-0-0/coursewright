"""The loading indicator covers every outstanding background request."""

from threading import Event

import pytest
from textual.app import App

from coursewright.core.canvas.models import CanvasCourse
from coursewright.ui.tui.screens.course_list import CourseListScreen
from coursewright.ui.tui.state import AppState


class OverlappingClient:
    def __init__(self):
        self.started = [Event(), Event()]
        self.release = [Event(), Event()]
        self.calls = 0

    def get_course_average_grades(self, course_ids):
        request_index = self.calls
        self.calls += 1
        self.started[request_index].set()
        assert self.release[request_index].wait(10), "Test did not release Canvas request"
        return {}

    def get_assignments(self, course_id):
        return []

    def get_active_submissions_for_assignments(self, course_id, assignment_ids):
        return []


@pytest.mark.asyncio
async def test_overlapping_refresh_keeps_indicator_until_both_finish():
    client = OverlappingClient()
    course = CanvasCourse(id=7, name="Sample Course", course_code="TEST101")
    state = AppState(canvas_client=client, courses=[course], filtered_courses=[course])
    screen = CourseListScreen(state)

    class ActivityApp(App):
        def on_mount(self):
            self.push_screen(screen)

    async with ActivityApp().run_test() as pilot:
        async def wait_until(condition):
            for _ in range(100):
                if condition():
                    return
                await pilot.pause(0.02)
            raise AssertionError("Timed out waiting for background worker")

        try:
            await wait_until(client.started[0].is_set)
            screen._refresh_average_grade_async()
            await wait_until(client.started[1].is_set)
            client.release[0].set()
            await wait_until(
                lambda: screen._background_request_counts.get("average grade") == 1
            )
            assert screen.query_one("#background-activity").display
            client.release[1].set()
            await wait_until(lambda: not screen.query_one("#background-activity").display)
        finally:
            for release in client.release:
                release.set()
