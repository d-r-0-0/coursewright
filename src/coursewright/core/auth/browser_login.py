"""Browser-based Canvas login.

Ported from discussorator ``auth/browser_login.py``.  Drives a headed
Chromium browser to the Canvas base URL, waits for successful
authentication (detected via the ``canvas_session`` cookie), harvests
storage state, and persists the resulting session through
:class:`~coursewright.core.auth.browser_session.BrowserSessionManager`.

WHY WE NAVIGATE TO THE BARE BASE URL (NOT /login/canvas):
Navigating directly to ``/login/canvas`` presents the *local* username /
password form, which **bypasses SSO and 2FA flows** used by many
institutions.  The bare base URL lets Canvas redirect the browser
through whatever authentication provider the institution has configured.

PLAYWRIGHT IS AN OPTIONAL DEPENDENCY:
Playwright installs a full browser suite (~300 MB) and is not required
for everyday use, so it lives in the ``browser`` extra:

    pip install "coursewright[browser]"
    playwright install chromium

It is imported lazily, inside :func:`_load_sync_playwright`, so this
module imports - and its tests run - without Playwright installed.

LIBRARY CODE IS SILENT (AGENTS.md 3.8): discussorator printed Rich
panels and prompted for Enter from here.  This port returns a
:class:`BrowserLoginResult` instead, reports progress through an
optional callback, and asks the caller (``ui/``) to supply the
"press Enter when logged in" prompt as a callable.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from coursewright.core.auth.browser_session import (
    BrowserSessionManager,
    get_browser_session_manager,
)

if TYPE_CHECKING:
    # Only imported at type-check time so type checkers benefit from the
    # Playwright stubs without an ImportError at runtime when it is absent.
    from playwright.sync_api import BrowserContext, Page

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How long (seconds) to wait for the canvas_session cookie to appear after
# the user completes their institution's login flow.
DEFAULT_AUTH_TIMEOUT_SECONDS: int = 300  # 5 minutes - generous for SSO / MFA

# Cookie whose presence signals that Canvas has fully authenticated the user.
CANVAS_SESSION_COOKIE_NAME: str = "canvas_session"

# How often (milliseconds) to poll for the auth cookie.
POLL_INTERVAL_MS: int = 1_000  # 1 second

PLAYWRIGHT_INSTALL_COMMANDS: tuple[str, ...] = (
    'pip install "coursewright[browser]"',
    "playwright install chromium",
)


# ---------------------------------------------------------------------------
# Data returned to the caller
# ---------------------------------------------------------------------------


class BrowserLoginOutcome(StrEnum):
    """Every way :func:`run_browser_login` can end."""

    SUCCESS = "success"
    PLAYWRIGHT_MISSING = "playwright_missing"
    INVALID_URL = "invalid_url"
    NOT_AUTHENTICATED = "not_authenticated"
    PERSISTENCE_FAILED = "persistence_failed"


@dataclass(frozen=True)
class BrowserLoginResult:
    """
    What happened during a browser login, as data for ``ui/`` to render.

    It deliberately carries no cookies and no CSRF token: the session
    goes straight to the session manager and is never returned.
    Truthiness mirrors discussorator's boolean return value.
    """

    outcome: BrowserLoginOutcome
    message: str
    canvas_url: str | None = None

    @property
    def succeeded(self) -> bool:
        """Return True when the session was harvested and persisted."""
        return self.outcome is BrowserLoginOutcome.SUCCESS

    def __bool__(self) -> bool:
        return self.succeeded


class BrowserLoginStage(StrEnum):
    """Progress points the caller can render (panel, spinner, log line)."""

    STARTING = "starting"
    BROWSER_OPENED = "browser_opened"
    POLLING = "polling"


@dataclass(frozen=True)
class BrowserLoginProgress:
    """
    One progress notification from the login flow.

    ``completed_polls`` / ``total_polls`` are set for ``POLLING`` so the
    caller can show known-length progress (docs/HOUSE_STYLE.md 3).
    """

    stage: BrowserLoginStage
    canvas_url: str
    requires_manual_confirmation: bool
    timeout_seconds: int
    completed_polls: int = 0
    total_polls: int = 0


ProgressReporter = Callable[[BrowserLoginProgress], None]


# ---------------------------------------------------------------------------
# Optional lazy import of Playwright
# ---------------------------------------------------------------------------


def _load_sync_playwright() -> Callable[[], Any] | None:
    """
    Return Playwright's ``sync_playwright``, or None if it is not installed.

    The import lives inside this function so a missing optional
    dependency becomes a ``PLAYWRIGHT_MISSING`` result instead of an
    ``ImportError`` at module load.  Tests patch this function.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_browser_login(
    canvas_base_url: str,
    timeout_seconds: int = DEFAULT_AUTH_TIMEOUT_SECONDS,
    headless: bool = False,
    session_manager: BrowserSessionManager | None = None,
    require_manual_confirmation: bool = True,
    wait_for_user_confirmation: Callable[[], None] | None = None,
    report_progress: ProgressReporter | None = None,
) -> BrowserLoginResult:
    """
    Launch a Chromium browser, navigate to *canvas_base_url*, wait for
    the user to authenticate, extract the session cookies and CSRF token,
    then persist the session via ``BrowserSessionManager``.

    Args:
        canvas_base_url:
            The bare Canvas root URL, e.g. ``https://canvas.example.edu``.
            Do **not** include a path like ``/login/canvas`` - that would
            bypass SSO / MFA flows.
        timeout_seconds:
            Maximum seconds to wait for the ``canvas_session`` cookie to
            appear.  Only used when *require_manual_confirmation* is
            ``False`` (the automated cookie-polling path).
        headless:
            When ``True`` the browser runs without a visible window.
            Useful only in automated tests - humans need the headed
            window to click through their institution's login page.
        session_manager:
            Where the harvested session is persisted.  Defaults to the
            application singleton.
        require_manual_confirmation:
            When ``True`` (the default) the browser stays open until
            *wait_for_user_confirmation* returns.  This is more reliable
            than auto-detecting the ``canvas_session`` cookie, which can
            appear *mid* SSO redirect - before the full session is
            established - and close the browser on a half-baked session.
            Set to ``False`` to use the automated cookie-polling path.
        wait_for_user_confirmation:
            Called once the browser is open; it must block until the
            person confirms they can see their Canvas dashboard (``ui/``
            passes its "press Enter" prompt).  Required when
            *require_manual_confirmation* is ``True``.
        report_progress:
            Optional callback given a :class:`BrowserLoginProgress` at
            each stage, so ``ui/`` can show what is happening.

    Returns:
        A :class:`BrowserLoginResult`; truthy only on success.

    Raises:
        ValueError: manual confirmation was required but no
            *wait_for_user_confirmation* callable was supplied.  This is
            a programming error in the caller, raised before any
            browser is launched.
    """
    if require_manual_confirmation and wait_for_user_confirmation is None:
        raise ValueError(
            "wait_for_user_confirmation is required when "
            "require_manual_confirmation is True"
        )

    # 1.  Ensure Playwright is installed before doing anything else.
    sync_playwright = _load_sync_playwright()
    if sync_playwright is None:
        return BrowserLoginResult(
            BrowserLoginOutcome.PLAYWRIGHT_MISSING,
            _playwright_missing_message(),
        )

    # 2.  Validate and normalise the base URL.
    normalised_url = _normalise_canvas_url(canvas_base_url)
    if normalised_url is None:
        return BrowserLoginResult(
            BrowserLoginOutcome.INVALID_URL,
            "The Canvas URL you provided does not look valid.\n"
            f"Received: {canvas_base_url!r}\n"
            "Please provide a full URL such as: https://canvas.example.edu",
        )

    def notify(stage: BrowserLoginStage, completed_polls: int = 0, total_polls: int = 0) -> None:
        if report_progress is not None:
            report_progress(
                BrowserLoginProgress(
                    stage=stage,
                    canvas_url=normalised_url,
                    requires_manual_confirmation=require_manual_confirmation,
                    timeout_seconds=timeout_seconds,
                    completed_polls=completed_polls,
                    total_polls=total_polls,
                )
            )

    # 3.  Tell the caller what is about to happen.
    notify(BrowserLoginStage.STARTING)

    # 4.  Launch the browser and drive the login flow.
    session_manager = session_manager or get_browser_session_manager()

    with sync_playwright() as playwright_instance:
        browser = playwright_instance.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            # Navigate to the *bare* Canvas base URL - this allows the server
            # to redirect through whatever SSO / 2FA flow is configured.
            page.goto(normalised_url)
            notify(BrowserLoginStage.BROWSER_OPENED)

            # 5.  Wait for the login to complete.
            #
            # Manual confirmation (default): the browser stays open and we
            # block on the caller's confirmation, then verify the session
            # cookie exists.  Automated: poll for the canvas_session cookie
            # until it appears or the timeout elapses.
            if require_manual_confirmation:
                authenticated = _wait_for_manual_confirmation(
                    context, wait_for_user_confirmation
                )
            else:
                authenticated = _wait_for_canvas_session_cookie(
                    page=page,
                    context=context,
                    timeout_seconds=timeout_seconds,
                    report_poll=lambda completed, total: notify(
                        BrowserLoginStage.POLLING, completed, total
                    ),
                )

            if not authenticated:
                return BrowserLoginResult(
                    BrowserLoginOutcome.NOT_AUTHENTICATED,
                    "Login was not completed.\n"
                    "No Canvas session cookie was detected, so nothing was "
                    "saved.  Please run the login again and confirm only "
                    "after you can see your Canvas dashboard.",
                    canvas_url=normalised_url,
                )

            # 6.  Harvest storage state (cookies + local-storage).
            storage_state: dict = context.storage_state()

            # 7.  Extract the decoded CSRF token from the cookies list.
            csrf_token = _extract_csrf_token(storage_state.get("cookies", []))

            # 8.  Build the session blob and persist it.
            session_blob = {
                "cookies": storage_state.get("cookies", []),
                "csrf_token": csrf_token,
            }

            success, message = session_manager.store_session(session_blob)

            if success:
                return BrowserLoginResult(
                    BrowserLoginOutcome.SUCCESS,
                    "Browser login successful.  Your Canvas session has been "
                    f"saved.\n{message}",
                    canvas_url=normalised_url,
                )
            return BrowserLoginResult(
                BrowserLoginOutcome.PERSISTENCE_FAILED,
                f"Failed to save session: {message}\n"
                "Authentication succeeded but the session could not be "
                "persisted.  Please check keyring / file permissions.",
                canvas_url=normalised_url,
            )

        finally:
            # Always close the browser so we do not leave a dangling window.
            context.close()
            browser.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalise_canvas_url(raw_url: str) -> str | None:
    """
    Validate and normalise a Canvas base URL.

    Normalisation steps:
    - Strip leading/trailing whitespace.
    - Ensure the URL has a scheme (``https://`` is assumed if absent).
    - Strip any path, query, or fragment so we always navigate to the root.

    Returns:
        A normalised URL string, or ``None`` if the input is not parseable.
    """
    raw_url = raw_url.strip()
    if not raw_url:
        return None

    # Prepend scheme if the user omitted it (e.g. "canvas.example.edu").
    if not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url

    try:
        parsed = urllib.parse.urlparse(raw_url)
    except ValueError:
        return None

    if not parsed.netloc:
        return None

    # Reconstruct with only scheme + netloc - drop path, query, fragment.
    return f"{parsed.scheme}://{parsed.netloc}"


def _has_canvas_session_cookie(context: BrowserContext) -> bool:
    """Return True if the context's cookie jar holds ``canvas_session``."""
    return any(
        cookie.get("name") == CANVAS_SESSION_COOKIE_NAME for cookie in context.cookies()
    )


def _wait_for_manual_confirmation(
    context: BrowserContext,
    wait_for_user_confirmation: Callable[[], None],
) -> bool:
    """
    Keep the browser open and block until the user confirms they have
    finished logging in, then verify the Canvas session cookie is present.

    WHY MANUAL CONFIRMATION IS THE DEFAULT:
    Auto-detecting the ``canvas_session`` cookie can fire *during* an SSO /
    2FA redirect - before the full Canvas session is actually established -
    which closed the browser too early and persisted a half-baked session
    that Canvas then rejected on the very next API call.  Letting the human
    confirm once they can see their dashboard guarantees every cookie is
    fully baked before we harvest the session.

    Returns:
        ``True`` if the ``canvas_session`` cookie is present after
        confirmation, ``False`` otherwise.
    """
    wait_for_user_confirmation()
    return _has_canvas_session_cookie(context)


def _wait_for_canvas_session_cookie(
    page: Page,
    context: BrowserContext,
    timeout_seconds: int,
    report_poll: Callable[[int, int], None] | None = None,
) -> bool:
    """
    Poll the browser context's cookie jar until the ``canvas_session``
    cookie appears, indicating that Canvas has authenticated the user.

    We use a busy-wait loop with :meth:`page.wait_for_timeout` (which
    keeps the page responsive between polls) rather than a single long
    wait for a URL or response, because SSO / MFA redirects can land on
    many intermediate URLs that are impossible to predict in advance.

    Args:
        page: The Playwright page object (used only for ``wait_for_timeout``).
        context: The browser context whose cookies we inspect.
        timeout_seconds: Maximum seconds before returning ``False``.
        report_poll: Optional ``(completed_polls, total_polls)`` callback.

    Returns:
        ``True`` if ``canvas_session`` was detected, ``False`` on timeout.
    """
    total_polls = int((timeout_seconds * 1_000) / POLL_INTERVAL_MS)

    for completed_polls in range(1, total_polls + 1):
        if _has_canvas_session_cookie(context):
            return True

        # Yield control to Playwright's event loop for one polling interval.
        page.wait_for_timeout(POLL_INTERVAL_MS)
        if report_poll is not None:
            report_poll(completed_polls, total_polls)

    return False


def _extract_csrf_token(cookies: list[dict]) -> str:
    """
    Extract and URL-decode the ``_csrf_token`` cookie value from the cookie
    list returned by :meth:`BrowserContext.storage_state`.

    Canvas stores the CSRF token as a URL-encoded cookie value.  Decoding it
    here means the stored session holds the raw token string, matching the
    format Canvas expects in ``X-CSRF-Token`` headers.

    Private: the value goes only into the blob handed to the session
    manager; it is never returned from the public API or logged.

    Returns:
        The decoded CSRF token string, or an empty string if the cookie was
        not found in the provided list.
    """
    for cookie in cookies:
        if cookie.get("name") == "_csrf_token":
            # Canvas URL-encodes the token value in the cookie.
            encoded_value: str = cookie.get("value", "")
            return urllib.parse.unquote(encoded_value)

    return ""


def _playwright_missing_message() -> str:
    """
    Return plain text telling the user how to install Playwright so the
    browser login becomes available.  ``ui/`` wraps it in a panel.
    """
    install_lines = "\n".join(f"  {command}" for command in PLAYWRIGHT_INSTALL_COMMANDS)
    return (
        "Playwright is not installed.\n"
        "The browser login feature requires Playwright and the Chromium "
        "browser binary.  To install them, run:\n"
        f"{install_lines}\n"
        "After installation, retry: coursewright auth login-browser"
    )
