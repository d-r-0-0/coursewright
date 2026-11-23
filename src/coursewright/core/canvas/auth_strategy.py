"""Authentication strategies for the Canvas LMS client.

Ported from discussorator ``canvas/client.py`` (``AuthStrategy``,
``TokenAuth``, ``SessionAuth``, ``CanvasAuthError``) and split into its
own module so ``client.py`` focuses on HTTP transport and API methods.

HOUSE STYLE (docs/HOUSE_STYLE.md section 1):
- Auth is a strategy object (``AuthStrategy`` with ``TokenAuth`` and
  ``SessionAuth``).
- The client never knows which kind it holds.
- A strategy's ``repr`` and ``str`` never show the secret.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import requests
from pydantic import SecretStr


class CanvasAuthError(Exception):
    """Raised when Canvas rejects authentication credentials."""


class AuthStrategy(ABC):
    """Abstract base class for Canvas authentication strategies.

    Every concrete strategy tells the HTTP session how to authenticate
    (via ``apply_to_session``) and exposes its fixed headers as a plain
    dict (via ``get_headers``) so the client can keep ``self.headers``
    in sync even when the session is replaced by a test mock.
    """

    @abstractmethod
    def get_headers(self) -> dict[str, str]:
        """Return the fixed HTTP headers this strategy contributes."""

    def apply_to_session(self, session: requests.Session) -> None:
        """Configure *session* with the credentials owned by this strategy."""
        session.headers.update(self.get_headers())

    @abstractmethod
    def describe(self) -> str:
        """Return a short, human-readable label for this auth mode."""

    def get_per_request_headers(self, method: str) -> dict[str, str]:
        """Return additional HTTP headers for a single outgoing request."""
        return {}


class TokenAuth(AuthStrategy):
    """Bearer-token authentication strategy for the Canvas API."""

    AUTHORIZATION_HEADER_NAME = "Authorization"

    def __init__(self, access_token: str | SecretStr) -> None:
        raw_token = (
            access_token.get_secret_value() if isinstance(access_token, SecretStr) else access_token
        )
        if not raw_token:
            raise ValueError(
                "TokenAuth requires a non-empty access_token.  "
                "Generate one at Canvas > Account > Settings > New Access Token."
            )
        self._access_token = SecretStr(raw_token)

    def get_headers(self) -> dict[str, str]:
        """Return the Authorization Bearer header dict for this token."""
        token_value = self._access_token.get_secret_value()
        return {self.AUTHORIZATION_HEADER_NAME: f"Bearer {token_value}"}

    def describe(self) -> str:
        """Return a human-readable label for the token auth mode."""
        return "API token (Bearer)"

    def __repr__(self) -> str:
        return "TokenAuth(token='****')"

    __str__ = __repr__


class SessionAuth(AuthStrategy):
    """Browser-session authentication strategy for the Canvas API.

    Installs captured browser cookies on the ``requests.Session`` and
    attaches the decoded ``X-CSRF-Token`` header on state-changing HTTP
    verbs (POST, PUT, DELETE, PATCH).
    """

    WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "DELETE", "PATCH"})
    CSRF_HEADER_NAME = "X-CSRF-Token"

    def __init__(
        self,
        cookies: list[dict[str, Any]],
        csrf_token: str | SecretStr,
    ) -> None:
        if not cookies:
            raise ValueError(
                "SessionAuth requires at least one cookie.  "
                "Capture cookies from a browser login first."
            )
        raw_csrf_token = (
            csrf_token.get_secret_value() if isinstance(csrf_token, SecretStr) else csrf_token
        )
        if not raw_csrf_token:
            raise ValueError(
                "SessionAuth requires a non-empty csrf_token.  "
                "URL-decode the '_csrf_token' cookie value before passing it here."
            )
        self._cookies: list[dict[str, Any]] = [dict(cookie) for cookie in cookies]
        self._csrf_token = SecretStr(raw_csrf_token)

    def get_headers(self) -> dict[str, str]:
        """Return fixed per-session headers (empty for cookie-based auth)."""
        return {}

    def apply_to_session(self, session: requests.Session) -> None:
        """Install all captured browser cookies onto *session*."""
        for cookie in self._cookies:
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path"),
            )

    def get_per_request_headers(self, method: str) -> dict[str, str]:
        """Return the CSRF header for state-changing requests; empty for reads."""
        if method.upper() in self.WRITE_METHODS:
            return {self.CSRF_HEADER_NAME: self._csrf_token.get_secret_value()}
        return {}

    def describe(self) -> str:
        """Return a human-readable label for the browser-session auth mode."""
        return "Browser session (cookies)"

    def __repr__(self) -> str:
        cookie_count = len(self._cookies)
        return f"SessionAuth(cookies={cookie_count}, csrf_token='****')"

    __str__ = __repr__
