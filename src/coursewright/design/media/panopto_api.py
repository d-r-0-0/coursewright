"""The one Panopto Public API client for Coursewright (HOUSE_STYLE.md §1).

Ported from COURSE_BUILDER ``lib/panopto_api.py`` and reshaped to the house
API client model:
- **One client class per remote service** (``PanoptoClient``, aliased as
  ``PanoptoAPI``).
- **Auth is a strategy object** (``PanoptoAuthStrategy``), whose ``repr``
  never shows the secret.
- **One choke point**: every HTTP round trip goes through
  ``PanoptoClient._make_request``, which enforces the 30-second timeout
  ceiling, logs safely without echoing secrets, and publishes traffic
  events to ``coursewright.core.traffic``.
- **One pagination helper**: list endpoints go through ``_paginated_get``.
- **Typed results**: public methods return ``PanoptoSession`` Pydantic v2
  models that retain dictionary compatibility for existing callers.
- **Configured folder IDs and neutral defaults**: no institution-specific
  hosts or hardcoded course folder IDs.
"""

from __future__ import annotations

import html
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from pydantic import SecretStr

from coursewright.core.traffic import (
    STATUS_ERRORED,
    STATUS_IN_FLIGHT,
    publish_http_round_trip,
)
from coursewright.design.media.panopto_auth import (
    PANOPTO_KEYRING_SERVICE,
    PANOPTO_LEGACY_KEY_PATH,
    AuthStrategy,
    ClientCredentialsAuth,
    PanoptoAuthStatus,
    PanoptoAuthStrategy,
    PanoptoClientCredentials,
    PanoptoClientCredentialsAuth,
    PanoptoNoAuth,
    PanoptoPasswordGrantAuth,
    PanoptoTokenAuth,
    PasswordGrantAuth,
    TokenAuth,
    check_panopto_client_status,
    load_panopto_client_credentials,
    login_panopto_client,
    store_panopto_client_credentials,
)
from coursewright.design.media.panopto_models import (
    PanoptoFolderDetails,
    PanoptoSession,
    PanoptoSessionUrls,
)

logger = logging.getLogger(__name__)

DEFAULT_PANOPTO_BASE_URL = "https://panopto.example.edu/Panopto"
REQUEST_TIMEOUT_SECONDS = 30
TRAFFIC_SOURCE = "panopto"


def parse_panopto_session_id(url: str) -> str | None:
    """Extract the Panopto session GUID from a viewer or embed URL.

    URLs scraped out of Canvas page bodies arrive HTML-escaped, so the
    separators are ``&amp;`` rather than ``&``. Without unescaping,
    ``parse_qs`` reads the second parameter as ``amp;id`` and reports no
    session when the video is fine.
    """
    parsed = urlparse(html.unescape(url.strip()))
    query = parse_qs(parsed.query)
    candidate = query.get("id", [""])[0].strip()
    return candidate or None


def sanitize_filename_component(value: str) -> str:
    """Return a filesystem-safe ASCII-ish filename fragment."""
    safe_characters = [
        char if (char.isalnum() or char in {"-", "_", "."}) else "_" for char in value
    ]
    collapsed = "".join(safe_characters).strip("_")
    return collapsed or "untitled"


def _normalize_panopto_base_url(base_url: str) -> str:
    """Normalize a base URL or bare host into ``https://<host>/Panopto``."""
    cleaned = base_url.strip().rstrip("/")
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    parsed = urlparse(cleaned)
    if not parsed.path or parsed.path == "/":
        cleaned = f"{cleaned}/Panopto"
    return cleaned


class PanoptoClient:
    """House-style Panopto Public API client for session metadata and captions."""

    REQUEST_TIMEOUT_SECONDS = REQUEST_TIMEOUT_SECONDS

    def __init__(
        self,
        base_url: str = DEFAULT_PANOPTO_BASE_URL,
        credentials: (
            PanoptoClientCredentials | PanoptoAuthStrategy | str | SecretStr | None
        ) = None,
        timeout_seconds: int | float | str = REQUEST_TIMEOUT_SECONDS,
        *,
        auth_strategy: PanoptoAuthStrategy | None = None,
        folder_id: str | None = None,
        rate_limit_delay: float = 0.0,
    ) -> None:
        self.base_url = _normalize_panopto_base_url(base_url)
        self.folder_id = folder_id
        self._rate_limit_delay = max(0.0, float(rate_limit_delay))

        # Support legacy 3-string positional call: PanoptoAPI(host, id, secret).
        if isinstance(credentials, str) and isinstance(timeout_seconds, str):
            pair = PanoptoClientCredentials(credentials, timeout_seconds)
            self.credentials: PanoptoClientCredentials | None = pair
            self._auth_strategy: PanoptoAuthStrategy = PanoptoClientCredentialsAuth(pair)
            effective_timeout = float(self.REQUEST_TIMEOUT_SECONDS)
        else:
            numeric_timeout = float(timeout_seconds)
            effective_timeout = (
                min(numeric_timeout, float(self.REQUEST_TIMEOUT_SECONDS))
                if numeric_timeout > 0
                else float(self.REQUEST_TIMEOUT_SECONDS)
            )
            self.credentials = (
                credentials if isinstance(credentials, PanoptoClientCredentials) else None
            )
            self._auth_strategy = self._resolve_auth_strategy(credentials, auth_strategy)

        self.timeout_seconds = effective_timeout
        self._session = requests.Session()
        self._auth_strategy.apply_to_session(self._session)

    @staticmethod
    def _resolve_auth_strategy(
        credentials: (PanoptoClientCredentials | PanoptoAuthStrategy | str | SecretStr | None),
        auth_strategy: PanoptoAuthStrategy | None,
    ) -> PanoptoAuthStrategy:
        if auth_strategy is not None:
            return auth_strategy
        if isinstance(credentials, PanoptoAuthStrategy):
            return credentials
        if isinstance(credentials, PanoptoClientCredentials):
            return PanoptoClientCredentialsAuth(credentials)
        if isinstance(credentials, str | SecretStr):
            return PanoptoTokenAuth(credentials)
        resolved = load_panopto_client_credentials("server")
        if resolved is not None:
            return PanoptoClientCredentialsAuth(resolved)
        raise ValueError("PanoptoClient requires credentials or an explicit auth_strategy")

    @property
    def auth_strategy(self) -> PanoptoAuthStrategy:
        """Return the active authentication strategy."""
        return self._auth_strategy

    @property
    def token_endpoint(self) -> str:
        """Return the OAuth token endpoint for the configured Panopto site."""
        return f"{self.base_url}/oauth2/connect/token"

    @property
    def api_base_url(self) -> str:
        """Return the Panopto public API base URL."""
        return f"{self.base_url}/api/v1"

    def __repr__(self) -> str:
        return f"PanoptoClient(base_url={self.base_url!r}, auth_strategy={self._auth_strategy!r})"

    # ------------------------------------------------------------------
    # The single request choke point (HOUSE_STYLE.md §1 & §3)
    # ------------------------------------------------------------------

    def _make_request(
        self,
        method: str,
        url: str,
        *,
        include_auth_headers: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Execute one HTTP request with a 30s timeout ceiling and traffic events."""
        if self._rate_limit_delay > 0:
            time.sleep(self._rate_limit_delay)

        requested_timeout = kwargs.pop("timeout", self.timeout_seconds)
        kwargs["timeout"] = min(float(requested_timeout), float(self.REQUEST_TIMEOUT_SECONDS))

        if include_auth_headers:
            strategy_headers = self._auth_strategy.get_per_request_headers(method, self)
            caller_headers = kwargs.get("headers") or {}
            kwargs["headers"] = {**strategy_headers, **caller_headers}

        # Signed download paths and resource names can themselves be private.
        request_path = urlparse(url).path
        if "/api/v1/folders/" in request_path:
            traffic_target = "folders/[id]/sessions"
        elif request_path.endswith("/sessions/search"):
            traffic_target = "sessions/search"
        elif "/api/v1/sessions/" in request_path:
            traffic_target = "sessions/[id]"
        elif request_path.endswith("/oauth2/connect/token"):
            traffic_target = "oauth2/connect/token"
        else:
            traffic_target = "download-or-probe"
        request_started_at = time.monotonic()
        publish_http_round_trip(
            TRAFFIC_SOURCE,
            method,
            traffic_target,
            STATUS_IN_FLIGHT,
            request_started_at,
        )
        try:
            response = self._session.request(method, url, **kwargs)
        except requests.RequestException as err:
            publish_http_round_trip(
                TRAFFIC_SOURCE,
                method,
                traffic_target,
                STATUS_ERRORED,
                request_started_at,
            )
            logger.error(
                "Panopto %s request failed (%s)",
                method.upper(),
                type(err).__name__,
            )
            raise err from None

        publish_http_round_trip(
            TRAFFIC_SOURCE,
            method,
            traffic_target,
            response.status_code,
            request_started_at,
        )
        if response.status_code >= 400:
            logger.error("Panopto %s returned HTTP %s", method, response.status_code)
        return response

    def _get_access_token(self) -> str:
        """Fetch and cache an access token through the configured strategy."""
        if hasattr(self._auth_strategy, "get_access_token"):
            return self._auth_strategy.get_access_token(self)  # type: ignore[no-any-return]
        headers = self._auth_strategy.get_headers(self)
        auth_header = headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header.removeprefix("Bearer ").strip()
        raise RuntimeError("Active Panopto auth strategy does not provide a token")

    def _authorized_headers(self) -> dict[str, str]:
        """Build authorization headers for Panopto API requests."""
        return self._auth_strategy.get_headers(self)

    def authenticate(self) -> bool:
        """Pre-authenticate with Panopto, logging failures without raw bodies."""
        try:
            self._get_access_token()
            return True
        except (RuntimeError, requests.RequestException):
            logger.error("Panopto authentication failed; check credentials and access")
            return False

    # ------------------------------------------------------------------
    # Retry and pagination helpers
    # ------------------------------------------------------------------

    def _get_with_retry(
        self,
        url: str,
        params: dict[str, str] | None = None,
        attempts: int = 5,
    ) -> requests.Response:
        """GET a Panopto endpoint through ``_make_request``, retrying 429 and 5xx."""
        delay = 1.0
        response: requests.Response | None = None

        for attempt in range(attempts):
            response = self._make_request("GET", url, params=params)
            if response.status_code < 500 and response.status_code != 429:
                return response
            if attempt == attempts - 1:
                break

            retry_after = response.headers.get("Retry-After")
            pause = float(retry_after) if retry_after else delay
            time.sleep(pause)
            delay *= 2

        assert response is not None
        return response

    def _paginated_get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        max_results: int = 100,
        pagination_mode: str = "continuation",
    ) -> list[PanoptoSession]:
        """Fetch a paginated list of Panopto sessions (continuation or pageNumber)."""
        found: list[PanoptoSession] = []
        base_params = dict(params or {})

        if pagination_mode == "continuation":
            continuation: str | None = None
            while len(found) < max_results:
                page_params = dict(base_params)
                if continuation:
                    page_params["continuationToken"] = continuation
                response = self._make_request("GET", url, params=page_params)
                response.raise_for_status()
                payload = response.json()
                results = payload.get("Results") or []
                if not results:
                    break
                found.extend(PanoptoSession.from_api_payload(r) for r in results)
                continuation = (payload.get("Continuation") or {}).get("NextContinuationToken")
                if not continuation:
                    break
            return found[:max_results]

        page = 0
        while len(found) < max_results:
            page_params = {**base_params, "pageNumber": str(page)}
            response = self._get_with_retry(url, params=page_params)
            response.raise_for_status()
            results = response.json().get("Results") or []
            if not results:
                break
            known_ids = {session.id for session in found}
            fresh = [
                PanoptoSession.from_api_payload(item)
                for item in results
                if item.get("Id") not in known_ids
            ]
            if not fresh:
                break
            found.extend(fresh)
            page += 1

        return found[:max_results]

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> PanoptoSession:
        """Return typed Panopto session metadata for the given session GUID."""
        response = self._make_request("GET", f"{self.api_base_url}/sessions/{session_id}")
        response.raise_for_status()
        return PanoptoSession.from_api_payload(response.json())

    def search_sessions(self, query: str, max_results: int = 100) -> list[PanoptoSession]:
        """Search sessions the credentialed account can see."""
        return self._paginated_get(
            f"{self.api_base_url}/sessions/search",
            params={"searchQuery": query},
            max_results=max_results,
            pagination_mode="continuation",
        )

    def list_folder_sessions(
        self, folder_id: str | None = None, max_results: int = 500
    ) -> list[PanoptoSession]:
        """List every session in the specified (or configured) folder."""
        target_folder_id = (folder_id or self.folder_id or "").strip()
        if not target_folder_id:
            raise ValueError("A Panopto folder_id must be passed or configured on the client")
        return self._paginated_get(
            f"{self.api_base_url}/folders/{target_folder_id}/sessions",
            max_results=max_results,
            pagination_mode="page_number",
        )

    def download_caption_file(self, caption_url: str, destination_path: Path) -> None:
        """Download a caption file to disk, trying unsigned then Bearer-authenticated."""
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        attempts = [False, True]
        last_error: Exception | None = None

        for include_auth in attempts:
            try:
                response = self._make_request(
                    "GET",
                    caption_url,
                    include_auth_headers=include_auth,
                )
                response.raise_for_status()
                destination_path.write_bytes(response.content)
                return None
            except requests.RequestException as err:
                last_error = err

        if last_error is None:
            raise RuntimeError("Panopto caption download failed without details")
        raise RuntimeError(str(last_error)) from last_error


def create_panopto_client(
    base_url: str = DEFAULT_PANOPTO_BASE_URL,
    *,
    client_type: str = "server",
    auth_strategy: PanoptoAuthStrategy | None = None,
    folder_id: str | None = None,
    timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
) -> PanoptoClient:
    """Factory that builds a ``PanoptoClient`` resolving credentials via ``core/auth``."""
    if auth_strategy is not None:
        return PanoptoClient(
            base_url,
            timeout_seconds=timeout_seconds,
            auth_strategy=auth_strategy,
            folder_id=folder_id,
        )
    credentials = load_panopto_client_credentials(client_type)
    if credentials is not None:
        return PanoptoClient(
            base_url,
            credentials=credentials,
            timeout_seconds=timeout_seconds,
            folder_id=folder_id,
        )
    raise RuntimeError(f"No Panopto {client_type} credentials found")


PanoptoAPI = PanoptoClient

__all__ = [
    "AuthStrategy",
    "ClientCredentialsAuth",
    "DEFAULT_PANOPTO_BASE_URL",
    "PANOPTO_KEYRING_SERVICE",
    "PANOPTO_LEGACY_KEY_PATH",
    "PanoptoAPI",
    "PanoptoAuthStatus",
    "PanoptoAuthStrategy",
    "PanoptoClient",
    "PanoptoClientCredentials",
    "PanoptoClientCredentialsAuth",
    "PanoptoFolderDetails",
    "PanoptoNoAuth",
    "PanoptoPasswordGrantAuth",
    "PanoptoSession",
    "PanoptoSessionUrls",
    "PanoptoTokenAuth",
    "PasswordGrantAuth",
    "REQUEST_TIMEOUT_SECONDS",
    "TRAFFIC_SOURCE",
    "TokenAuth",
    "check_panopto_client_status",
    "create_panopto_client",
    "load_panopto_client_credentials",
    "login_panopto_client",
    "parse_panopto_session_id",
    "sanitize_filename_component",
    "store_panopto_client_credentials",
]
