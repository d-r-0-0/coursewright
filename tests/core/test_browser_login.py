"""
Tests for ``coursewright.core.auth.browser_login``.

Ported from discussorator's ``test_browser_login.py``.  Every Playwright
object is a ``unittest.mock`` double, so these tests need neither the
optional ``browser`` extra nor a Chromium binary.

Deliberate changes from the source tests:

* ``run_browser_login`` returns a ``BrowserLoginResult`` (truthy on
  success) instead of a bare bool, so assertions check ``.outcome``.
* Prompting moved out of the library: instead of patching
  ``console.input`` the tests pass ``wait_for_user_confirmation``.
* Playwright is loaded through ``_load_sync_playwright``, which the
  tests patch (the source patched a module-level ``sync_playwright``).
* The "Rich panel does not crash" test became a test of the plain-text
  install message, since printing now lives in ``ui/``.
"""

from __future__ import annotations

import sys
import urllib.parse
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from coursewright.core.auth.browser_login import (
    CANVAS_SESSION_COOKIE_NAME,
    BrowserLoginOutcome,
    BrowserLoginProgress,
    BrowserLoginStage,
    _extract_csrf_token,
    _load_sync_playwright,
    _normalise_canvas_url,
    _playwright_missing_message,
    run_browser_login,
)

LOADER = "coursewright.core.auth.browser_login._load_sync_playwright"
CANVAS_URL = "https://canvas.example.edu"
SESSION_COOKIE = {"name": CANVAS_SESSION_COOKIE_NAME, "value": "s123"}


@pytest.fixture
def mock_session_manager():
    """A MagicMock standing in for BrowserSessionManager."""
    manager = MagicMock()
    manager.store_session.return_value = (True, "Session stored in keyring")
    return manager


@pytest.fixture
def confirm():
    """A confirmation callback that returns immediately."""
    return MagicMock(return_value=None)


@pytest.fixture
def playwright_factory():
    """Build a fake ``sync_playwright`` whose browser reports given cookies."""

    def _make(cookies=None, storage_state=None):
        effective_cookies = cookies if cookies is not None else []
        effective_storage = (
            storage_state
            if storage_state is not None
            else {"cookies": effective_cookies, "origins": []}
        )
        page = MagicMock()
        context = MagicMock()
        context.new_page.return_value = page
        context.cookies.return_value = effective_cookies
        context.storage_state.return_value = effective_storage
        browser = MagicMock()
        browser.new_context.return_value = context
        instance = MagicMock()
        instance.chromium.launch.return_value = browser

        @contextmanager
        def _sync_playwright():
            yield instance

        _sync_playwright.page = page
        _sync_playwright.context = context
        _sync_playwright.browser = browser
        return _sync_playwright

    return _make


def _login(factory, manager, confirm, **kwargs):
    with patch(LOADER, return_value=factory):
        return run_browser_login(
            canvas_base_url=kwargs.pop("canvas_base_url", CANVAS_URL),
            session_manager=manager,
            wait_for_user_confirmation=confirm,
            **kwargs,
        )


class TestNormaliseCanvasUrl:
    def test_strips_path_from_url(self):
        assert _normalise_canvas_url(f"{CANVAS_URL}/login/canvas") == CANVAS_URL

    def test_strips_query_and_fragment(self):
        raw = f"{CANVAS_URL}/courses?foo=bar#section"
        assert _normalise_canvas_url(raw) == CANVAS_URL

    def test_prepends_https_when_scheme_missing(self):
        assert _normalise_canvas_url("canvas.example.edu") == CANVAS_URL

    def test_empty_string_returns_none(self):
        assert _normalise_canvas_url("") is None

    def test_whitespace_only_returns_none(self):
        assert _normalise_canvas_url("   ") is None

    def test_valid_https_url_preserved(self):
        url = "https://school.instructure.com"
        assert _normalise_canvas_url(url) == url

    def test_surrounding_whitespace_is_stripped(self):
        assert _normalise_canvas_url(f"  {CANVAS_URL}  ") == CANVAS_URL


class TestExtractCsrfToken:
    def test_extracts_and_decodes_token(self):
        raw_value = urllib.parse.quote("abc+def/ghi==")
        cookies = [{"name": "_csrf_token", "value": raw_value}]
        assert _extract_csrf_token(cookies) == "abc+def/ghi=="

    def test_returns_empty_string_when_cookie_absent(self):
        cookies = [{"name": "canvas_session", "value": "xyz"}]
        assert _extract_csrf_token(cookies) == ""

    def test_returns_empty_string_for_empty_cookie_list(self):
        assert _extract_csrf_token([]) == ""

    def test_handles_already_decoded_token(self):
        cookies = [{"name": "_csrf_token", "value": "plainsimpletoken"}]
        assert _extract_csrf_token(cookies) == "plainsimpletoken"


class TestMissingPlaywright:
    def test_result_is_playwright_missing(self, mock_session_manager, confirm):
        with patch(LOADER, return_value=None):
            result = run_browser_login(
                canvas_base_url=CANVAS_URL,
                session_manager=mock_session_manager,
                wait_for_user_confirmation=confirm,
            )
        assert result.outcome is BrowserLoginOutcome.PLAYWRIGHT_MISSING
        assert not result
        confirm.assert_not_called()

    def test_message_explains_install(self):
        message = _playwright_missing_message()
        assert "playwright install chromium" in message
        assert "coursewright[browser]" in message

    def test_loader_returns_none_without_playwright(self):
        # A None entry in sys.modules makes the import raise ImportError.
        with patch.dict(sys.modules, {"playwright.sync_api": None}):
            assert _load_sync_playwright() is None


class TestInvalidUrl:
    def test_empty_url_is_rejected(
        self, mock_session_manager, confirm, playwright_factory
    ):
        result = _login(
            playwright_factory(), mock_session_manager, confirm, canvas_base_url=""
        )
        assert result.outcome is BrowserLoginOutcome.INVALID_URL
        mock_session_manager.store_session.assert_not_called()


class TestSuccess:
    def test_successful_login_stores_session(
        self, mock_session_manager, confirm, playwright_factory
    ):
        factory = playwright_factory(cookies=[SESSION_COOKIE])
        result = _login(factory, mock_session_manager, confirm)
        assert result
        assert result.outcome is BrowserLoginOutcome.SUCCESS
        assert result.canvas_url == CANVAS_URL
        mock_session_manager.store_session.assert_called_once()

    def test_manual_confirmation_is_awaited(
        self, mock_session_manager, confirm, playwright_factory
    ):
        factory = playwright_factory(cookies=[SESSION_COOKIE])
        _login(factory, mock_session_manager, confirm)
        confirm.assert_called_once()

    def test_manual_confirmation_requires_callback(self, mock_session_manager):
        with pytest.raises(ValueError, match="wait_for_user_confirmation"):
            run_browser_login(CANVAS_URL, session_manager=mock_session_manager)

    def test_missing_cookie_after_confirmation_saves_nothing(
        self, mock_session_manager, confirm, playwright_factory
    ):
        result = _login(playwright_factory(cookies=[]), mock_session_manager, confirm)
        assert result.outcome is BrowserLoginOutcome.NOT_AUTHENTICATED
        mock_session_manager.store_session.assert_not_called()

    def test_automated_path_polls_without_prompting(
        self, mock_session_manager, confirm, playwright_factory
    ):
        factory = playwright_factory(cookies=[SESSION_COOKIE])
        result = _login(
            factory, mock_session_manager, confirm, require_manual_confirmation=False
        )
        assert result
        confirm.assert_not_called()

    def test_blob_has_cookies_and_decoded_csrf(
        self, mock_session_manager, confirm, playwright_factory
    ):
        cookies = [
            SESSION_COOKIE,
            {"name": "_csrf_token", "value": urllib.parse.quote("csrf==value")},
        ]
        _login(playwright_factory(cookies=cookies), mock_session_manager, confirm)
        stored_blob = mock_session_manager.store_session.call_args[0][0]
        assert stored_blob["cookies"] == cookies
        assert stored_blob["csrf_token"] == "csrf==value"

    def test_result_carries_no_session_data(
        self, mock_session_manager, confirm, playwright_factory
    ):
        cookies = [
            {"name": CANVAS_SESSION_COOKIE_NAME, "value": "very-secret-cookie"},
            {"name": "_csrf_token", "value": "very-secret-csrf"},
        ]
        result = _login(
            playwright_factory(cookies=cookies), mock_session_manager, confirm
        )
        rendered = repr(result) + result.message
        assert "very-secret-cookie" not in rendered
        assert "very-secret-csrf" not in rendered

    def test_browser_and_context_closed_after_success(
        self, mock_session_manager, confirm, playwright_factory
    ):
        factory = playwright_factory(cookies=[SESSION_COOKIE])
        _login(factory, mock_session_manager, confirm)
        factory.context.close.assert_called_once()
        factory.browser.close.assert_called_once()

    def test_browser_closed_even_when_not_authenticated(
        self, mock_session_manager, confirm, playwright_factory
    ):
        factory = playwright_factory(cookies=[])
        _login(factory, mock_session_manager, confirm)
        factory.context.close.assert_called_once()
        factory.browser.close.assert_called_once()

    def test_navigates_to_bare_base_url(
        self, mock_session_manager, confirm, playwright_factory
    ):
        factory = playwright_factory(cookies=[SESSION_COOKIE])
        _login(
            factory,
            mock_session_manager,
            confirm,
            canvas_base_url=f"{CANVAS_URL}/login/canvas",
        )
        factory.page.goto.assert_called_once_with(CANVAS_URL)

    def test_progress_stages_reported(
        self, mock_session_manager, confirm, playwright_factory
    ):
        events: list[BrowserLoginProgress] = []
        factory = playwright_factory(cookies=[SESSION_COOKIE])
        _login(factory, mock_session_manager, confirm, report_progress=events.append)
        assert [event.stage for event in events] == [
            BrowserLoginStage.STARTING,
            BrowserLoginStage.BROWSER_OPENED,
        ]


class TestTimeout:
    def test_cookie_never_appears(
        self, mock_session_manager, confirm, playwright_factory
    ):
        events: list[BrowserLoginProgress] = []
        factory = playwright_factory(cookies=[])
        result = _login(
            factory,
            mock_session_manager,
            confirm,
            timeout_seconds=2,
            require_manual_confirmation=False,
            report_progress=events.append,
        )
        assert result.outcome is BrowserLoginOutcome.NOT_AUTHENTICATED
        mock_session_manager.store_session.assert_not_called()
        polls = [e for e in events if e.stage is BrowserLoginStage.POLLING]
        assert [(p.completed_polls, p.total_polls) for p in polls] == [(1, 2), (2, 2)]


class TestPersistenceFailure:
    def test_store_session_failure_is_reported(
        self, mock_session_manager, confirm, playwright_factory
    ):
        mock_session_manager.store_session.return_value = (False, "Keyring locked")
        factory = playwright_factory(cookies=[SESSION_COOKIE])
        result = _login(factory, mock_session_manager, confirm)
        assert result.outcome is BrowserLoginOutcome.PERSISTENCE_FAILED
        assert "Keyring locked" in result.message
