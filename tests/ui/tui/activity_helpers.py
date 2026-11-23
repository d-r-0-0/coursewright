"""Remote activity is always visible in the Textual app.

docs/HOUSE_STYLE.md section 3 and the card's acceptance item 4: any
screen that triggers a remote call shows the transmission log or a
loading indicator while the call runs.  Each test here holds a fake
Canvas call open on a ``threading.Event`` so the "while it runs" state
can be inspected with the Textual pilot, then releases it.

Also covers the application shell: its name and the ``tui`` command.
"""

from __future__ import annotations

from threading import Event
from typing import Any

from textual.app import App
from textual.screen import Screen

from coursewright.ui.tui.state import AppState

# Long enough for a slow CI machine, short enough to fail fast on a hang.
WAIT_ATTEMPTS = 100
WAIT_STEP_SECONDS = 0.05


async def wait_until(pilot, condition, description: str) -> None:
    """Pause the pilot until ``condition()`` holds, or fail naming it."""
    for _attempt in range(WAIT_ATTEMPTS):
        if condition():
            return
        await pilot.pause(WAIT_STEP_SECONDS)
    raise AssertionError(f"Timed out waiting for: {description}")


def find_screen(app, screen_class: type[Screen]) -> Any:
    """Return the first screen of ``screen_class`` on the app's stack."""
    for screen in app.screen_stack:
        if isinstance(screen, screen_class):
            return screen
    return None


class OneScreenApp(App):
    """A bare app that starts on a screen chosen by the test.

    It deliberately does not subclass ``CoursewrightApp``: Textual runs
    ``on_mount`` handlers of every base class, which would also push the
    real ``InitScreen`` and reach for real credentials.
    """

    def __init__(self, first_screen: Screen, app_state: AppState | None = None) -> None:
        super().__init__()
        self._first_screen = first_screen
        self.app_state = app_state or AppState()

    def on_mount(self) -> None:
        self.push_screen(self._first_screen)


class HeldCanvasClient:
    """Canvas fake whose calls block until the test releases them."""

    def __init__(self, release: Event, courses: list[dict] | None = None) -> None:
        self.release = release
        self.courses = courses or []
        self.entered = Event()

    def get_courses(self) -> list[dict]:
        self.entered.set()
        self.release.wait(timeout=5)
        return self.courses

    def get_assignments(self, course_id: int) -> list:
        return []

    def get_active_submissions_for_assignments(self, course_id, assignment_ids) -> list:
        return []

    def get_last_student_participation_by_course(self, course_ids) -> dict:
        self.entered.set()
        self.release.wait(timeout=5)
        return {}


def make_course_dict(course_id: int = 7) -> dict:
    return {
        "id": course_id,
        "name": "Sample Course",
        "course_code": "ABC101",
        "workflow_state": "available",
        "term": {"name": "Fall 2024"},
        "total_students": 3,
    }
