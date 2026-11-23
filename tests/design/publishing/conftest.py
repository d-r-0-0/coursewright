"""Fixtures for the publishing tests (the fakes live in publishing_fakes)."""

from __future__ import annotations

import pytest
from publishing_fakes import BASE_URL, FakeCanvasServer

from coursewright.core.canvas.client import CanvasClient


@pytest.fixture
def server() -> FakeCanvasServer:
    return FakeCanvasServer()


@pytest.fixture
def client(server: FakeCanvasServer) -> CanvasClient:
    canvas_client = CanvasClient(BASE_URL, "synthetic-token")
    canvas_client._session = server.session
    canvas_client._rate_limit_delay = 0
    return canvas_client
