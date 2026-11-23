"""Strict paginated reads for the publishing tools.

A deletion plan built from a silently truncated list would delete too little
and report success.  The shared client now raises ``CanvasRequestError`` on a
failed read; this module turns every incomplete read into
:class:`CanvasReadError` so publishing callers have one error to handle.
COURSE_BUILDER's ``delete_announcements`` had its own loop that reports the
failure; this is that loop, written once.

It goes through the client's single request choke point
(``CanvasClient._make_request``), so rate limiting, the 30-second ceiling and
traffic events still apply (HOUSE_STYLE.md section 1).
"""

from __future__ import annotations

from typing import Any

from coursewright.core.canvas.client import CanvasClient, CanvasRequestError


class CanvasReadError(Exception):
    """A Canvas read failed, so any list built from it would be incomplete."""


def fetch_every_page_strictly(
    client: CanvasClient,
    first_page_url: str,
    first_page_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return every item of a paginated list, or raise :class:`CanvasReadError`."""
    page_url: str | None = first_page_url
    page_params = dict(first_page_params) if first_page_params else None
    every_item: list[dict[str, Any]] = []
    while page_url:
        try:
            response = client._make_request("GET", page_url, params=page_params)
        except CanvasRequestError as err:
            raise CanvasReadError(f"Failed to fetch {first_page_url} ({err}).") from err
        if response is None or response.status_code != 200:
            status = "no response" if response is None else f"code {response.status_code}"
            raise CanvasReadError(f"Failed to fetch {first_page_url} ({status}).")
        try:
            page = response.json()
        except ValueError as err:
            raise CanvasReadError(f"Failed to fetch {first_page_url} (invalid JSON).") from err
        if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
            raise CanvasReadError(f"Failed to fetch {first_page_url} (unexpected response).")
        every_item.extend(page)
        page_url = response.links.get("next", {}).get("url")
        page_params = None  # the next-page URL already carries its query string
    return every_item


def fetch_course_collection(
    client: CanvasClient, course_id: int, resource_path: str
) -> list[dict[str, Any]]:
    """Read a complete course collection before making a safety decision.

    Keep the source's strict pagination until the shared client offers a
    strict variant; its normal list methods return partial data on failure.
    All HTTP still passes through the shared client's request choke point.
    """
    return fetch_every_page_strictly(
        client,
        f"{client.base_url}/api/v1/courses/{course_id}/{resource_path}",
        {"per_page": 100},
    )


def fetch_every_announcement(client: CanvasClient, course_id: int) -> list[dict[str, Any]]:
    """Return every announcement of a course, or raise :class:`CanvasReadError`."""
    return fetch_every_page_strictly(
        client,
        f"{client.base_url}/api/v1/courses/{course_id}/discussion_topics",
        {"only_announcements": "true", "per_page": 100},
    )
