"""Fixtures: a real ``CanvasClient`` whose HTTP goes to recorded payloads."""

from __future__ import annotations

import pytest
import requests
from synthetic_canvas import BASE_URL, RecordedCanvas, build_routes

from coursewright.core.canvas.client import CanvasClient


@pytest.fixture
def recorded_canvas(monkeypatch: pytest.MonkeyPatch) -> RecordedCanvas:
    """The recorded Canvas; override ``routes`` entries per test."""
    recorded = RecordedCanvas(routes=build_routes())
    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda self, method, url, **kwargs: recorded.respond(method, url, **kwargs),
    )
    return recorded


@pytest.fixture
def canvas(recorded_canvas: RecordedCanvas) -> CanvasClient:
    """A real client whose HTTP goes to ``recorded_canvas``."""
    client = CanvasClient(BASE_URL, "synthetic-token")
    client._rate_limit_delay = 0
    return client
