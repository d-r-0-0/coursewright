"""Offline Panopto transport and real traffic-bus observation."""

import json
from unittest.mock import Mock

import pytest
import requests

from coursewright.core.traffic import bus


@pytest.fixture
def panopto_transport(monkeypatch):
    transport = Mock(side_effect=AssertionError("Unexpected HTTP call"))
    monkeypatch.setattr(requests.Session, "request", transport)
    return transport


@pytest.fixture
def http_response():
    def make_response(status=200, payload=None, content=None, headers=None):
        response = requests.Response()
        response.status_code = status
        response._content = content if content is not None else json.dumps(payload).encode()
        response.headers.update(headers or {})
        response.url = "https://panopto.example.edu/Panopto/api/v1/sessions/example"
        return response

    return make_response


@pytest.fixture
def traffic_events():
    events = []
    bus.subscribe(events.append)
    yield events
    bus.unsubscribe(events.append)
