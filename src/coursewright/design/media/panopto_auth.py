"""Panopto authentication strategies and silent credential status helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import requests
from pydantic import SecretStr

from coursewright.core.auth import StorageMethod
from coursewright.core.auth.panopto import (
    PANOPTO_KEYRING_SERVICE,
    PANOPTO_LEGACY_KEY_PATH,
    PanoptoClientCredentials,
    load_panopto_client_credentials,
    store_panopto_client_credentials,
)
from coursewright.design.media.panopto_models import DictCompatibleModel

if TYPE_CHECKING:
    from coursewright.design.media.panopto_api import PanoptoClient

__all__ = [
    "PANOPTO_KEYRING_SERVICE",
    "PANOPTO_LEGACY_KEY_PATH",
    "PanoptoClientCredentials",
    "load_panopto_client_credentials",
    "store_panopto_client_credentials",
    "PanoptoAuthStrategy",
    "PanoptoTokenAuth",
    "PanoptoClientCredentialsAuth",
    "PanoptoPasswordGrantAuth",
    "PanoptoNoAuth",
    "PanoptoAuthStatus",
    "check_panopto_client_status",
    "login_panopto_client",
    "AuthStrategy",
    "TokenAuth",
    "ClientCredentialsAuth",
    "PasswordGrantAuth",
    "NoAuth",
]


class PanoptoAuthStatus(DictCompatibleModel):
    """Result of a silent Panopto credential login or status check."""

    success: bool
    client_type: str
    source: str | None = None
    storage_method: StorageMethod = StorageMethod.NONE
    message: str

    @property
    def exit_code(self) -> int:
        """Return 0 on success, 1 on failure."""
        return 0 if self.success else 1


def login_panopto_client(
    client_id: str,
    client_secret: str,
    client_type: str = "server",
    scope: str = "api",
) -> PanoptoAuthStatus:
    """Validate and store a Panopto credential pair without echoing values."""
    cleaned_id = client_id.strip()
    cleaned_secret = client_secret.strip()
    if not cleaned_id or not cleaned_secret:
        return PanoptoAuthStatus(
            success=False,
            client_type=client_type,
            message="Both client ID and secret are required. Nothing stored.",
        )
    try:
        store_panopto_client_credentials(
            PanoptoClientCredentials(cleaned_id, cleaned_secret, scope=scope),
            client_type,
        )
    except (ValueError, RuntimeError):
        return PanoptoAuthStatus(
            success=False,
            client_type=client_type,
            message="Unable to store Panopto credentials. Check the OS vault backend.",
        )
    return PanoptoAuthStatus(
        success=True,
        client_type=client_type,
        source="vault",
        storage_method=StorageMethod.KEYRING,
        message=f"Panopto {client_type} credential pair stored in the OS vault.",
    )


def check_panopto_client_status(client_type: str = "server") -> PanoptoAuthStatus:
    """Check local Panopto credential availability without network calls."""
    try:
        credentials = load_panopto_client_credentials(client_type)
    except (ValueError, RuntimeError, OSError):
        return PanoptoAuthStatus(
            success=False,
            client_type=client_type,
            message="Panopto credential resolution failed. Check the selected source.",
        )
    if credentials is None:
        return PanoptoAuthStatus(
            success=False,
            client_type=client_type,
            message=f"No complete Panopto {client_type} credential pair found.",
        )
    method_map = {
        "vault": StorageMethod.KEYRING,
        "environment": StorageMethod.ENVIRONMENT,
        "legacy-file": StorageMethod.FILE,
    }
    return PanoptoAuthStatus(
        success=True,
        client_type=client_type,
        source=credentials.source,
        storage_method=method_map.get(credentials.source, StorageMethod.NONE),
        message=(
            f"Panopto {client_type} credential pair source: {credentials.source}. "
            "This checks storage only; API validity has not been tested."
        ),
    )


class PanoptoAuthStrategy(ABC):
    """Abstract authentication strategy for the Panopto client (HOUSE_STYLE §1)."""

    @abstractmethod
    def get_headers(self, client: PanoptoClient | None = None) -> dict[str, str]:
        """Return HTTP authorization headers for an outgoing Panopto request."""

    def apply_to_session(self, session: requests.Session) -> None:
        """Apply any static session-level headers or cookies."""
        return None

    def get_per_request_headers(
        self, method: str, client: PanoptoClient | None = None
    ) -> dict[str, str]:
        """Return headers for a single request, authenticating through ``client``."""
        return self.get_headers(client)

    @abstractmethod
    def describe(self) -> str:
        """Return a short human-readable description of this auth mode."""


class PanoptoTokenAuth(PanoptoAuthStrategy):
    """Static Bearer-token authentication strategy for Panopto."""

    def __init__(self, access_token: str | SecretStr) -> None:
        raw = (
            access_token.get_secret_value() if isinstance(access_token, SecretStr) else access_token
        )
        if not raw or not raw.strip():
            raise ValueError("PanoptoTokenAuth requires a non-empty access_token")
        self._access_token = SecretStr(raw.strip())

    def get_headers(self, client: PanoptoClient | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token.get_secret_value()}",
            "Accept": "application/json",
        }

    def describe(self) -> str:
        return "Panopto bearer token"

    def __repr__(self) -> str:
        return "PanoptoTokenAuth(token='****')"


class PanoptoClientCredentialsAuth(PanoptoAuthStrategy):
    """OAuth2 client-credentials strategy; fetches token via client choke point."""

    def __init__(self, credentials: PanoptoClientCredentials) -> None:
        self.credentials = credentials
        self._access_token: SecretStr | None = None

    def get_access_token(self, client: PanoptoClient) -> str:
        """Fetch and cache an OAuth2 client-credentials token via ``client``."""
        if self._access_token is not None:
            return self._access_token.get_secret_value()

        request_body = {
            "grant_type": "client_credentials",
            "scope": self.credentials.scope,
        }
        attempts = [
            {
                "data": request_body,
                "auth": (
                    self.credentials.client_id,
                    self.credentials.client_secret,
                ),
            },
            {
                "data": {
                    **request_body,
                    "client_id": self.credentials.client_id,
                    "client_secret": self.credentials.client_secret,
                },
                "auth": None,
            },
        ]
        failure_messages: list[str] = []
        for attempt in attempts:
            response = client._make_request(
                "POST",
                client.token_endpoint,
                data=attempt["data"],
                auth=attempt["auth"],
                include_auth_headers=False,
            )
            if response.ok:
                payload = response.json()
                access_token = (payload.get("access_token") or "").strip()
                if not access_token:
                    raise RuntimeError("Panopto token response did not include access_token")
                self._access_token = SecretStr(access_token)
                return access_token
            failure_messages.append(f"HTTP {response.status_code}")

        joined_failures = " | ".join(failure_messages)
        raise RuntimeError(f"Panopto token request failed: {joined_failures}")

    def get_headers(self, client: PanoptoClient | None = None) -> dict[str, str]:
        if client is None and self._access_token is None:
            return {"Accept": "application/json"}
        token = (
            self._access_token.get_secret_value()
            if self._access_token is not None
            else self.get_access_token(client)  # type: ignore[arg-type]
        )
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def describe(self) -> str:
        return "Panopto OAuth2 client credentials"

    def __repr__(self) -> str:
        return (
            f"PanoptoClientCredentialsAuth(scope={self.credentials.scope!r}, "
            f"source={self.credentials.source!r})"
        )


class PanoptoPasswordGrantAuth(PanoptoAuthStrategy):
    """OAuth2 password-grant strategy for user-scoped Panopto diagnostics."""

    def __init__(
        self,
        credentials: PanoptoClientCredentials,
        username: str,
        password: str | SecretStr,
        scope: str = "api offline_access",
    ) -> None:
        raw_password = password.get_secret_value() if isinstance(password, SecretStr) else password
        if not username.strip() or not raw_password:
            raise ValueError("A complete Panopto user login is required")
        self.credentials = credentials
        self._username = SecretStr(username.strip())
        self._password = SecretStr(raw_password)
        self.scope = scope
        self._access_token: SecretStr | None = None

    def get_access_token(self, client: PanoptoClient) -> str:
        """Fetch and cache a password-grant access token via ``client``."""
        if self._access_token is not None:
            return self._access_token.get_secret_value()
        response = client._make_request(
            "POST",
            client.token_endpoint,
            auth=(self.credentials.client_id, self.credentials.client_secret),
            data={
                "grant_type": "password",
                "username": self._username.get_secret_value(),
                "password": self._password.get_secret_value(),
                "scope": self.scope,
            },
            include_auth_headers=False,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Authentication failed: HTTP {response.status_code}")
        access_token = (response.json().get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("Authentication response omitted the access token")
        self._access_token = SecretStr(access_token)
        return access_token

    def get_headers(self, client: PanoptoClient | None = None) -> dict[str, str]:
        if client is None and self._access_token is None:
            return {"Accept": "application/json"}
        token = (
            self._access_token.get_secret_value()
            if self._access_token is not None
            else self.get_access_token(client)  # type: ignore[arg-type]
        )
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def describe(self) -> str:
        return "Panopto OAuth2 password grant"

    def __repr__(self) -> str:
        return f"PanoptoPasswordGrantAuth(scope={self.scope!r}, source={self.credentials.source!r})"


class PanoptoNoAuth(PanoptoAuthStrategy):
    """Explicit anonymous strategy for public (org-shared) Panopto pages.

    The viewer page and the SRT caption endpoint answer without a token
    for sessions shared with the whole organisation.  Selecting this
    strategy deliberately keeps the client off the keyring: no
    credentials are looked up, and no Authorization header is sent.
    """

    def get_headers(self, client: PanoptoClient | None = None) -> dict[str, str]:
        return {}

    def describe(self) -> str:
        return "Panopto anonymous (public pages, no credentials)"

    def __repr__(self) -> str:
        return "PanoptoNoAuth()"


AuthStrategy = PanoptoAuthStrategy
TokenAuth = PanoptoTokenAuth
ClientCredentialsAuth = PanoptoClientCredentialsAuth
PasswordGrantAuth = PanoptoPasswordGrantAuth
NoAuth = PanoptoNoAuth
