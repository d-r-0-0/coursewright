"""
Tests for SessionAuth in canvas/client.py
==========================================

PURPOSE:
--------
These tests verify the ``SessionAuth`` concrete strategy introduced by
slice AUTH-B3.  ``SessionAuth`` authenticates via browser-captured
cookies and a URL-decoded CSRF token, replicating what Canvas's own web
UI does:
  - Every request carries the browser session cookies.
  - POST / PUT / DELETE / PATCH requests additionally carry an
    ``X-CSRF-Token`` header.
  - GET / HEAD requests carry cookies only (no CSRF header).

All tests mock ``requests.Session`` -- no real network calls are made.

Test inventory:
1.  ``SessionAuth`` instantiates correctly with valid cookies + token.
2.  Empty cookie list raises ``ValueError`` at construction time.
3.  Empty CSRF token string raises ``ValueError`` at construction time.
4.  ``get_headers`` returns an empty dict (no fixed per-session headers).
5.  ``apply_to_session`` installs each cookie onto the session's CookieJar.
6.  ``describe`` returns a sensible human-readable label.
7.  ``__repr__`` does not expose the full CSRF token or cookie values.
8.  ``get_per_request_headers`` returns CSRF header for POST.
9.  ``get_per_request_headers`` returns CSRF header for PUT.
10. ``get_per_request_headers`` returns CSRF header for DELETE.
11. ``get_per_request_headers`` returns CSRF header for PATCH.
12. ``get_per_request_headers`` returns empty dict for GET.
13. ``get_per_request_headers`` returns empty dict for HEAD.
14. ``EnhancedCanvasAPI`` with ``SessionAuth`` sends CSRF header on POST.
15. ``EnhancedCanvasAPI`` with ``SessionAuth`` omits CSRF header on GET.
16. ``TokenAuth`` is unaffected by ``get_per_request_headers`` (returns {}).
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from coursewright.core.canvas.client import (
    EnhancedCanvasAPI,
    SessionAuth,
    TokenAuth,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# A minimal set of cookies that resembles what Canvas sets after a browser
# login.  Only ``name`` and ``value`` are mandatory; ``domain``/``path`` are
# optional extras we include to test that ``apply_to_session`` passes them.
SAMPLE_COOKIES: list[dict] = [
    {
        "name": "log_session_id",
        "value": "abc123",
        "domain": "canvas.example.edu",
        "path": "/",
    },
    {
        "name": "_csrf_token",
        "value": "5Xb4RandomToken==",
        "domain": "canvas.example.edu",
        "path": "/",
    },
]

# The URL-decoded value of the ``_csrf_token`` cookie.  In production this
# is obtained via ``urllib.parse.unquote(raw_cookie_value)``.
SAMPLE_CSRF_TOKEN = "5Xb4RandomToken=="


# ---------------------------------------------------------------------------
# Tests for SessionAuth in isolation
# ---------------------------------------------------------------------------


class TestSessionAuthConstruction:
    """Tests for ``SessionAuth.__init__`` validation."""

    def test_valid_construction_stores_cookies_and_token(self):
        """
        ``SessionAuth`` must accept a non-empty cookie list and a non-empty
        CSRF token and store them internally without raising.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        # Verify the strategy stored what we passed (indirectly via behaviour).
        assert strategy.describe()  # should not raise; confirms object is usable

    def test_empty_cookies_raises_value_error(self):
        """
        An empty cookie list must raise ``ValueError`` at construction time,
        preventing silent unauthenticated requests.
        """
        with pytest.raises(ValueError, match="cookie"):
            SessionAuth(cookies=[], csrf_token=SAMPLE_CSRF_TOKEN)

    def test_empty_csrf_token_raises_value_error(self):
        """
        An empty CSRF token string must raise ``ValueError`` at construction
        time so we don't silently send requests without CSRF protection.
        """
        with pytest.raises(ValueError, match="csrf_token"):
            SessionAuth(cookies=SAMPLE_COOKIES, csrf_token="")


class TestSessionAuthHeaders:
    """Tests for ``get_headers`` and ``get_per_request_headers``."""

    def test_get_headers_returns_empty_dict(self):
        """
        ``get_headers`` must return an empty dict because ``SessionAuth``
        carries no fixed per-session headers -- auth is handled by cookies
        (set on the session) and the per-request CSRF header.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        result = strategy.get_headers()

        assert result == {}
        # Must be a plain dict, not a subclass or MagicMock.
        assert type(result) is dict  # noqa: E721 -- strict type check intentional

    def test_get_per_request_headers_returns_csrf_for_post(self):
        """
        POST is a state-changing verb, so ``get_per_request_headers``
        must include ``X-CSRF-Token``.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        headers = strategy.get_per_request_headers("POST")

        assert headers.get("X-CSRF-Token") == SAMPLE_CSRF_TOKEN

    def test_get_per_request_headers_returns_csrf_for_put(self):
        """PUT is state-changing; CSRF header must be present."""
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        headers = strategy.get_per_request_headers("PUT")

        assert headers.get("X-CSRF-Token") == SAMPLE_CSRF_TOKEN

    def test_get_per_request_headers_returns_csrf_for_delete(self):
        """DELETE is state-changing; CSRF header must be present."""
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        headers = strategy.get_per_request_headers("DELETE")

        assert headers.get("X-CSRF-Token") == SAMPLE_CSRF_TOKEN

    def test_get_per_request_headers_returns_csrf_for_patch(self):
        """PATCH is state-changing; CSRF header must be present."""
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        headers = strategy.get_per_request_headers("PATCH")

        assert headers.get("X-CSRF-Token") == SAMPLE_CSRF_TOKEN

    def test_get_per_request_headers_returns_empty_for_get(self):
        """
        GET is a safe/read-only method.  Canvas does not require CSRF
        protection for reads, so the header must be absent.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        headers = strategy.get_per_request_headers("GET")

        assert headers == {}

    def test_get_per_request_headers_returns_empty_for_head(self):
        """HEAD is a safe method; no CSRF header should be added."""
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        headers = strategy.get_per_request_headers("HEAD")

        assert headers == {}

    def test_get_per_request_headers_is_case_insensitive_for_method(self):
        """
        ``_make_request`` in ``EnhancedCanvasAPI`` normalises verbs to
        upper-case, but ``get_per_request_headers`` should handle both
        cases robustly because it calls ``.upper()`` internally.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        # Lower-case "post" should still produce the CSRF header.
        headers = strategy.get_per_request_headers("post")

        assert headers.get("X-CSRF-Token") == SAMPLE_CSRF_TOKEN


class TestSessionAuthApplyToSession:
    """Tests for ``apply_to_session`` -- cookie installation."""

    def test_apply_to_session_sets_all_cookies(self):
        """
        ``apply_to_session`` must install every cookie from the captured
        list onto the ``requests.Session``.  We use a real session here
        to validate the actual CookieJar behaviour.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        real_session = requests.Session()
        strategy.apply_to_session(real_session)

        # Every cookie in our sample list must be present in the session jar.
        installed_names = {cookie.name for cookie in real_session.cookies}
        for cookie in SAMPLE_COOKIES:
            assert cookie["name"] in installed_names, (
                f"Cookie '{cookie['name']}' was not installed on the session"
            )

    def test_apply_to_session_preserves_cookie_values(self):
        """
        The value of each installed cookie must match what was captured
        from the browser -- no modification, URL-encoding, or truncation.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        real_session = requests.Session()
        strategy.apply_to_session(real_session)

        for cookie in SAMPLE_COOKIES:
            assert real_session.cookies.get(cookie["name"]) == cookie["value"], (
                f"Cookie '{cookie['name']}' has wrong value after apply_to_session"
            )

    def test_apply_to_session_does_not_set_auth_header(self):
        """
        Unlike ``TokenAuth``, ``SessionAuth.apply_to_session`` must NOT add
        any ``Authorization`` header -- auth is cookie-based only.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        real_session = requests.Session()
        strategy.apply_to_session(real_session)

        assert "Authorization" not in real_session.headers


class TestSessionAuthMeta:
    """Tests for ``describe`` and ``__repr__``."""

    def test_describe_returns_human_readable_label(self):
        """
        ``describe`` must return a non-empty string that mentions both
        "session" and "cookies" so the TUI status panel is unambiguous.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        label = strategy.describe()

        assert isinstance(label, str)
        assert len(label) > 0
        label_lower = label.lower()
        assert "session" in label_lower or "cookies" in label_lower, (
            f"describe() should mention 'session' or 'cookies', got: {label!r}"
        )

    def test_repr_does_not_expose_full_csrf_token(self):
        """
        ``__repr__`` must mask the CSRF token so it cannot accidentally
        appear in log files or tracebacks.
        """
        long_csrf = "VerySecretCSRFToken123"
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=long_csrf,
        )
        representation = repr(strategy)

        # The full token must NOT appear verbatim.
        assert long_csrf not in representation
        assert long_csrf[:4] not in representation
        assert long_csrf not in str(strategy)
        # The repr should still identify the class.
        assert "SessionAuth" in representation

    def test_repr_does_not_expose_cookie_values(self):
        """
        ``__repr__`` must not reveal cookie values.  It may report the
        count of cookies but must not dump raw values.
        """
        sensitive_cookie_value = "super-secret-session-id-9999"
        cookies_with_secret = [{"name": "log_session_id", "value": sensitive_cookie_value}]
        strategy = SessionAuth(
            cookies=cookies_with_secret,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        representation = repr(strategy)

        assert sensitive_cookie_value not in representation
        assert sensitive_cookie_value not in str(strategy)


# ---------------------------------------------------------------------------
# Integration tests: SessionAuth wired through EnhancedCanvasAPI
# ---------------------------------------------------------------------------


class TestEnhancedCanvasAPIWithSessionAuth:
    """
    Verify that ``EnhancedCanvasAPI._make_request`` correctly injects the
    CSRF header when ``SessionAuth`` is the active strategy.

    All tests patch ``requests.Session`` to avoid real HTTP calls.
    """

    def _build_client_with_mocked_session(self):
        """
        Helper: construct an ``EnhancedCanvasAPI`` with ``SessionAuth`` and
        return ``(client, mock_session)`` so tests can inspect call args.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        with patch("coursewright.core.canvas.client.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            # ``_make_request`` reads ``self._session.request(...)``
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "{}"
            mock_session.request.return_value = mock_response

            client = EnhancedCanvasAPI(
                "https://canvas.example.edu",
                auth_strategy=strategy,
            )
            client._session = mock_session  # swap in mock after __init__
        return client, mock_session

    def test_post_request_includes_csrf_header(self):
        """
        ``_make_request("POST", ...)`` must pass ``X-CSRF-Token`` in the
        ``headers`` kwarg forwarded to ``session.request``.
        """
        client, mock_session = self._build_client_with_mocked_session()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "{}"
        mock_session.request.return_value = mock_response

        client._make_request("POST", "https://canvas.example.edu/api/v1/foo")

        # Retrieve the actual call kwargs.
        call_kwargs = mock_session.request.call_args
        # call_args is (args, kwargs), and ``session.request(method, url, **kwargs)``
        # so we need to check the ``headers`` keyword argument.
        actual_headers = call_kwargs.kwargs.get("headers", {})
        assert actual_headers.get("X-CSRF-Token") == SAMPLE_CSRF_TOKEN, (
            "POST request must carry X-CSRF-Token header"
        )

    def test_get_request_omits_csrf_header(self):
        """
        ``_make_request("GET", ...)`` must NOT include ``X-CSRF-Token``
        because GET is a safe/read-only method.
        """
        client, mock_session = self._build_client_with_mocked_session()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "[]"
        mock_session.request.return_value = mock_response

        client._make_request("GET", "https://canvas.example.edu/api/v1/bar")

        call_kwargs = mock_session.request.call_args
        actual_headers = call_kwargs.kwargs.get("headers", {})
        assert "X-CSRF-Token" not in actual_headers, (
            "GET request must NOT carry X-CSRF-Token header"
        )

    def test_delete_request_includes_csrf_header(self):
        """
        ``_make_request("DELETE", ...)`` must include ``X-CSRF-Token``
        because DELETE is a state-changing verb.
        """
        client, mock_session = self._build_client_with_mocked_session()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "{}"
        mock_session.request.return_value = mock_response

        client._make_request("DELETE", "https://canvas.example.edu/api/v1/baz")

        call_kwargs = mock_session.request.call_args
        actual_headers = call_kwargs.kwargs.get("headers", {})
        assert actual_headers.get("X-CSRF-Token") == SAMPLE_CSRF_TOKEN, (
            "DELETE request must carry X-CSRF-Token header"
        )

    def test_token_auth_get_per_request_headers_returns_empty(self):
        """
        ``TokenAuth.get_per_request_headers`` must return ``{}`` for any
        verb so it does not accidentally inject a CSRF header on existing
        token-based requests.

        This is a regression guard: the AUTH-B3 change must not break
        the original token-based auth flow.
        """
        strategy = TokenAuth("some-canvas-api-token")

        assert strategy.get_per_request_headers("GET") == {}
        assert strategy.get_per_request_headers("POST") == {}
        assert strategy.get_per_request_headers("DELETE") == {}

    def test_client_stores_session_auth_strategy(self):
        """
        When ``auth_strategy=SessionAuth(...)`` is passed, the client must
        store the exact strategy object so ``client._auth_strategy.describe()``
        returns the browser-session label.
        """
        strategy = SessionAuth(
            cookies=SAMPLE_COOKIES,
            csrf_token=SAMPLE_CSRF_TOKEN,
        )
        with patch("coursewright.core.canvas.client.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.headers = {}

            client = EnhancedCanvasAPI(
                "https://canvas.example.edu",
                auth_strategy=strategy,
            )

        assert client._auth_strategy is strategy
        assert isinstance(client._auth_strategy, SessionAuth)
        assert "session" in client._auth_strategy.describe().lower() or (
            "cookies" in client._auth_strategy.describe().lower()
        )
