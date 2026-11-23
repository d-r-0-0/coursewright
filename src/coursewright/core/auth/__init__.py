"""Credential lookup: OS keyring, environment, then file.

Secure credential management for Canvas API tokens and browser
sessions.  Tokens are handed out as masked ``pydantic.SecretStr``
values and session blobs as ``SessionBlob`` (masked ``repr``); nothing
in this package prints, prompts or logs a secret.

The browser login flow lives in ``coursewright.core.auth.browser_login``
and is imported from there, so importing this package never touches
the optional Playwright dependency.
"""

from __future__ import annotations

from coursewright.core.auth.browser_session import (
    BrowserSessionManager,
    SessionBlob,
    get_browser_session_manager,
)
from coursewright.core.auth.credentials import (
    CredentialManager,
    StorageMethod,
    TokenSource,
    get_api_token,
    get_credential_manager,
    store_api_token,
)

__all__ = [
    "BrowserSessionManager",
    "CredentialManager",
    "SessionBlob",
    "StorageMethod",
    "TokenSource",
    "get_api_token",
    "get_browser_session_manager",
    "get_credential_manager",
    "store_api_token",
]
