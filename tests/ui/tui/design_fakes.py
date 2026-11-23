"""Shared fakes for the course-design screen tests.

``FakeDesignCanvas`` is a small in-memory Canvas shell with synthetic
modules and items.  Every write is recorded in ``writes`` so a test can
prove a cancelled Apply wrote nothing, and every read publishes traffic
events the way the real client's request choke point does, so the
transmission log has something to show.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from textual.widgets import DataTable

from coursewright.core import traffic
from coursewright.ui.tui.screens.design_flow import CourseDesignScreen
from coursewright.ui.tui.state import AppState

from .activity_helpers import OneScreenApp, wait_until

COURSE_ID = 101
COURSE_NAME = "Example Design Course"
SCHEDULE_TEXT = "Unit\tAssigned\t1st Post\tDue\nUnit 01\t09/02/2030\t09/06/2030\t09/13/2030\n"


class FakeDesignCanvas:
    """In-memory Canvas; reads publish traffic, writes are recorded."""

    base_url = "https://canvas.example.edu"

    def __init__(self) -> None:
        self.writes: list[tuple[str, tuple[Any, ...]]] = []
        self.modules = [
            {"id": 1, "name": "Unit 01 Intro", "published": False, "unlock_at": None},
            {"id": 2, "name": "Unit 02 Next", "published": True, "unlock_at": None},
        ]
        self.items: dict[int, list[dict[str, Any]]] = {
            1: [
                {
                    "id": 11,
                    "title": "U01A01 Task",
                    "position": 1,
                    "type": "Assignment",
                    "content_id": 501,
                    "published": False,
                },
                {
                    "id": 12,
                    "title": "U01R01 Read",
                    "position": 2,
                    "type": "Page",
                    "content_id": None,
                    "published": False,
                },
            ],
            2: [],
        }

    def _trip(self, target: str) -> None:
        traffic.publish_http_round_trip("canvas", "GET", target, 200, 5.0)

    # -- reads ----------------------------------------------------------

    def get_course(self, course_id: int) -> dict[str, Any]:
        self._trip(f"courses/{course_id}")
        return {"id": course_id, "name": COURSE_NAME, "workflow_state": "available"}

    def get_modules(self, course_id: int) -> list[dict[str, Any]]:
        self._trip(f"courses/{course_id}/modules")
        return self.modules

    def get_module(self, course_id: int, module_id: int) -> dict[str, Any]:
        return next(m for m in self.modules if m["id"] == module_id)

    def get_module_items(self, course_id: int, module_id: int) -> list[dict[str, Any]]:
        self._trip(f"courses/{course_id}/modules/{module_id}/items")
        return self.items[module_id]

    def get_module_item(self, course_id: int, module_id: int, item_id: int) -> dict[str, Any]:
        return next(i for i in self.items[module_id] if i["id"] == item_id)

    def get_assignment(self, course_id: int, assignment_id: int) -> dict[str, Any]:
        return {"id": assignment_id}

    def get_assignments(self, course_id: int) -> list[Any]:
        return []

    get_discussion_topics = get_quizzes = get_announcements = get_pages = get_assignments
    get_assignment_groups = get_assignments

    def _paginated_get(self, url: str, params: dict[str, Any] | None = None) -> list[Any]:
        return []

    def get_page(self, course_id: int, page_url: str) -> None:
        return None

    def _make_request(self, method: str, url: str, params: Any = None) -> SimpleNamespace:
        """Serve the strict paginated reads that publishing uses."""
        self._trip("courses/modules")
        if url.endswith("/modules"):
            body: list[Any] = self.modules
        else:
            body = self.items[int(url.rsplit("/modules/", 1)[1].split("/")[0])]
        return SimpleNamespace(status_code=200, json=lambda: body, links={})

    # -- writes ---------------------------------------------------------

    def _record(self, name: str, *args: Any) -> dict[str, Any]:
        self.writes.append((name, args))
        return {"id": 900 + len(self.writes)}

    def update_module_item(self, *args: Any) -> dict[str, Any]:
        return self._record("update_module_item", *args)

    def update_module(self, *args: Any) -> dict[str, Any]:
        return self._record("update_module", *args)

    def update_assignment(self, *args: Any) -> dict[str, Any]:
        return self._record("update_assignment", *args)

    def update_quiz(self, *args: Any) -> dict[str, Any]:
        return self._record("update_quiz", *args)

    def create_discussion_topic(self, *args: Any) -> dict[str, Any]:
        return self._record("create_discussion_topic", *args)

    def create_module_item(self, *args: Any) -> dict[str, Any]:
        return self._record("create_module_item", *args)


def make_design_state(canvas: FakeDesignCanvas | None = None) -> AppState:
    state = AppState()
    state.canvas_client = canvas
    state.current_course = SimpleNamespace(id=COURSE_ID, name=COURSE_NAME)
    return state


def make_design_app(canvas: FakeDesignCanvas) -> OneScreenApp:
    state = make_design_state(canvas)
    return OneScreenApp(CourseDesignScreen(state), state)


def backup_files(workspace: Path) -> list[Path]:
    return list(workspace.rglob("*.json"))


def table_text(screen, selector: str = "#phase-table") -> str:
    """Every cell of a DataTable joined into one string, for assertions."""
    table = screen.query_one(selector, DataTable)
    return " ".join(str(cell) for row in range(table.row_count) for cell in table.get_row_at(row))


async def wait_for_design_screen(app, pilot) -> CourseDesignScreen:
    await wait_until(pilot, lambda: isinstance(app.screen, CourseDesignScreen), "design screen")
    await pilot.pause()  # let the new screen finish mounting its header
    return app.screen


async def preview_phase(app, pilot, number: int) -> CourseDesignScreen:
    """Select phase ``number`` and run its preview to completion."""
    screen = await wait_for_design_screen(app, pilot)
    await pilot.press(str(number))
    await pilot.press("v")
    key = screen.current_phase.key
    await wait_until(pilot, lambda: key in screen.previews, f"preview of phase {number}")
    await wait_until(pilot, lambda: app.screen is screen, "transmission log closed")
    return screen
