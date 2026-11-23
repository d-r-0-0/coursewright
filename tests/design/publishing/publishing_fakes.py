"""A fake Canvas server behind a real ``CanvasClient``.

The publishing tools are tested against the real client and the real safe
writer; only the HTTP session is replaced.  That way a test about "nothing is
deleted without execute" counts the HTTP methods that actually left the
client, not the calls a mock happened to receive.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import requests

BASE_URL = "https://canvas.example.edu"


def make_response(
    status_code: int = 200, payload: Any = None, link_header: str | None = None
) -> requests.Response:
    """Build a ``requests.Response`` carrying *payload* as JSON."""
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(payload if payload is not None else {}).encode()
    if link_header:
        response.headers["Link"] = link_header
    return response


class FakeCanvasServer:
    """Routes the client's HTTP calls to canned responses and records them.

    ``routes`` maps ``(METHOD, path-regex)`` to a response or a callable
    ``(url, kwargs) -> response``.  The first matching route wins; a route
    holding a list pops one response per call.  An unrouted request fails
    the test loudly instead of returning something plausible.
    """

    def __init__(self) -> None:
        self.session = Mock()
        self.session.request.side_effect = self._handle
        self.routes: list[tuple[str, re.Pattern[str], Any]] = []
        self.requests_sent: list[tuple[str, str]] = []
        self.before_request: Callable[[str, str], None] | None = None

    def route(self, method: str, path_regex: str, response: Any) -> None:
        self.routes.append((method, re.compile(path_regex + "$"), response))

    def _handle(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requests_sent.append((method, url))
        if self.before_request is not None:
            self.before_request(method, url)
        path = url.removeprefix(BASE_URL).split("?")[0]
        for route_method, pattern, response in self.routes:
            if route_method == method and pattern.match(path):
                if callable(response):
                    return response(url, kwargs)
                if isinstance(response, list):
                    return response.pop(0)
                return response
        raise AssertionError(f"Unrouted request: {method} {url}")

    @property
    def methods_sent(self) -> list[str]:
        return [method for method, _url in self.requests_sent]
