"""Recorded, synthetic Canvas payloads for the Phase 1 diagnostics tests.

``RecordedCanvas`` answers ``requests.Session.request`` from these payloads
and remembers every request; the fixtures live in ``conftest.py``.  No name,
id, host or course here is real.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

BASE_URL = "https://canvas.example.edu"
COURSE_ID = 101

PUBLISHER_NEW_NODE = (
    "https://gateway.publisher.example/rest/launchBasicLTI/1000001/"
    "2000001_1000001_9001/3000001/nodeId/11/start"
)
PUBLISHER_NEW_ETEXT = (
    "https://gateway.publisher.example/rest/launchBasicLTI/1000001/"
    "2000001_1000001_9001/3000002"
)
PUBLISHER_OLD_NODE = (
    "https://gateway.publisher.example/rest/launchBasicLTI/1000001/"
    "2000000_1000001_8001/3000099/nodeId/7/start"
)
LECTURE_TOOL_LAUNCH = "https://school.hosted.panopto.com/Panopto/LTI/LTI.aspx"

SYNTHETIC_COURSE = {
    "id": COURSE_ID,
    "name": "Synthetic Course A",
    "course_code": "SYN-100",
    "workflow_state": "available",
    "term": {"name": "Term One"},
    "syllabus_body": "<p>Office hours are Tuesday in Room 12.</p>",
}

SYNTHETIC_MODULES = [
    {
        "id": 11,
        "name": "Module One",
        "published": True,
        "unlock_at": "2030-01-06T00:00:00Z",
        "items": [{"id": 111, "title": "Welcome", "type": "Page", "published": True}],
    },
    {
        "id": 12,
        "name": "Module Two",
        "published": False,
        "unlock_at": None,
        "items": [],
    },
]

SYNTHETIC_MODULE_ITEMS = {
    11: [
        {"id": 111, "title": "Welcome", "type": "Page", "published": True},
        {
            "id": 112,
            "title": "Chapter Reading",
            "type": "ExternalTool",
            "published": True,
            "external_url": PUBLISHER_NEW_NODE,
        },
        {
            "id": 113,
            "title": "Lecture Recording",
            "type": "ExternalTool",
            "published": True,
            "external_url": LECTURE_TOOL_LAUNCH,
        },
    ],
    12: [
        {"id": 121, "title": "Live Lab 1-1 Old Style", "type": "ExternalTool",
         "published": False, "external_url": PUBLISHER_OLD_NODE},
        {"id": 122, "title": "Week 2 Discussion", "type": "Discussion"},
    ],
}

SYNTHETIC_ASSIGNMENTS = [
    {
        "id": 201,
        "name": "Lab With Due Date",
        "due_at": "2020-01-15T23:59:00Z",
        "assignment_group_id": 5,
        "description": "<p>Meet in Room 12.</p>",
        "external_tool_tag_attributes": {"url": PUBLISHER_NEW_NODE},
    },
    {
        "id": 202,
        "name": "Undated Reflection",
        "due_at": None,
        "assignment_group_id": 5,
        "description": None,
        "submission_types": ["online_text_entry"],
    },
    {
        "id": 203,
        "name": "Future Project",
        "due_at": "2999-01-01T23:59:00Z",
        "assignment_group_id": 5,
    },
]

SYNTHETIC_PAGES = [
    {"title": "Course Policies", "url": "course-policies"},
    {"title": "Late Work", "url": "late-work"},
]
SYNTHETIC_PAGE_BODIES = {
    "course-policies": "<h2>Policies</h2><p>Submit by <b>Friday</b>.</p>"
    '<a href="https://video.example.com/watch">video</a>',
    "late-work": "<p>Late work loses ten percent.</p>",
}


@dataclass
class RecordedCanvas:
    """Routes ``requests.Session.request`` to recorded payloads."""

    routes: dict[str, Any] = field(default_factory=dict)
    requests_seen: list[tuple[str, str]] = field(default_factory=list)

    def respond(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requests_seen.append((method, url))
        path = urlparse(url).path
        payload = self.routes.get(path)
        for pattern, candidate in self.routes.items():
            if payload is None and pattern.endswith("*") and path.startswith(pattern[:-1]):
                payload = candidate
        response = requests.Response()
        response.url = url
        if payload is None:
            response.status_code = 404
            response._content = b"{}"
        else:
            response.status_code = 200
            response._content = json.dumps(payload).encode()
        return response

    @property
    def methods_used(self) -> set[str]:
        return {method for method, _ in self.requests_seen}


def build_routes() -> dict[str, Any]:
    api = "/api/v1"
    course = f"{api}/courses/{COURSE_ID}"
    routes: dict[str, Any] = {
        f"{api}/courses": [SYNTHETIC_COURSE, {**SYNTHETIC_COURSE, "id": 99,
                                               "name": "Other Synthetic Course",
                                               "course_code": "SYN-200"}],
        course: SYNTHETIC_COURSE,
        f"{course}/modules": SYNTHETIC_MODULES,
        f"{course}/assignments": SYNTHETIC_ASSIGNMENTS,
        f"{course}/assignment_groups": [{"id": 5, "name": "Labs"}],
        f"{course}/quizzes": [{"id": 31, "title": "Quiz One", "description": "<p>Room 12</p>"}],
        f"{course}/discussion_topics": [
            {"id": 41, "title": "Week 2 Discussion", "message": "<p>Introduce yourself.</p>"}
        ],
        f"{api}/announcements": [
            {"id": 51, "title": "Hello", "message": "<p>Office hours moved.</p>"}
        ],
        f"{course}/pages": SYNTHETIC_PAGES,
    }
    for module_id, items in SYNTHETIC_MODULE_ITEMS.items():
        routes[f"{course}/modules/{module_id}/items"] = items
    for slug, body in SYNTHETIC_PAGE_BODIES.items():
        routes[f"{course}/pages/{slug}"] = {"title": slug, "url": slug, "body": body}
    return routes
