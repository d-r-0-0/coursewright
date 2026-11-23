"""Atomic Panopto OAuth credential pairs; no network or import-time I/O.

Server and user identities resolve independently: vault, environment,
then the legacy server-only file. Malformed populated tiers fail closed.
"""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import keyring

from coursewright.core.auth.credentials import CredentialManager

PANOPTO_KEYRING_SERVICE = CredentialManager.SERVICE_NAME
PANOPTO_LEGACY_KEY_PATH = Path.cwd() / "panopto.key"


@dataclass(frozen=True)
class PanoptoClientCredentials:
    """Container for Panopto OAuth client credentials (secrets hidden from repr)."""

    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    scope: str = "api"
    source: str = "provided"

    def as_tuple(self) -> tuple[str, str]:
        """Return ``(client_id, client_secret)`` for tuple-unpacking callers."""
        return (self.client_id, self.client_secret)

    def __iter__(self) -> Iterator[str]:
        """Allow ``client_id, client_secret = credentials`` unpacking."""
        yield self.client_id
        yield self.client_secret


def _panopto_credential_key(client_type: str) -> str:
    """Keep server and user clients in separate atomic vault entries."""
    if client_type not in {"server", "user"}:
        raise ValueError("Panopto client type must be server or user")
    return f"panopto_{client_type}_client"


def _validated_panopto_credentials(payload: object, source: str) -> PanoptoClientCredentials:
    """Validate a complete pair without including its values in errors."""
    if not isinstance(payload, dict):
        raise ValueError(f"Malformed Panopto credentials in {source}")
    client_id = payload.get("client_id")
    client_secret = payload.get("client_secret")
    scope = payload.get("scope", "api")
    if (
        not isinstance(client_id, str)
        or not client_id.strip()
        or not isinstance(client_secret, str)
        or not client_secret.strip()
        or not isinstance(scope, str)
        or not scope.strip()
    ):
        raise ValueError(f"Incomplete or malformed Panopto credentials in {source}")
    return PanoptoClientCredentials(client_id.strip(), client_secret.strip(), scope.strip(), source)


def load_panopto_client_credentials(
    client_type: str = "server",
) -> PanoptoClientCredentials | None:
    """Resolve one complete pair: vault, environment, then legacy file.

    A present but invalid tier fails closed. Backend unavailability warns
    before fallback. The untyped two-line legacy file is server-only; user
    OAuth must never silently borrow the server application's identity.
    """
    credential_key = _panopto_credential_key(client_type)
    try:
        vault_value = keyring.get_password(PANOPTO_KEYRING_SERVICE, credential_key)
    except Exception:
        warnings.warn(
            "Panopto credential vault unavailable; trying fallback sources",
            RuntimeWarning,
            stacklevel=2,
        )
        vault_value = None
    if vault_value is not None:
        try:
            payload = json.loads(vault_value)
        except (TypeError, ValueError):
            raise ValueError("Malformed Panopto credentials in vault") from None
        return _validated_panopto_credentials(payload, "vault")

    prefix = "PANOPTO" if client_type == "server" else "PANOPTO_USER"
    cw_prefix = f"COURSEWRIGHT_{prefix}"
    # A credential pair must come from one namespace, never half from each.
    if any(os.getenv(f"{cw_prefix}_{part}") is not None for part in ("CLIENT_ID", "CLIENT_SECRET")):
        prefix = cw_prefix
    client_id = os.getenv(f"{prefix}_CLIENT_ID")
    client_secret = os.getenv(f"{prefix}_CLIENT_SECRET")
    scope = os.getenv(f"{prefix}_SCOPE", "api").strip() or "api"
    if client_id is not None or client_secret is not None:
        return _validated_panopto_credentials(
            {"client_id": client_id, "client_secret": client_secret, "scope": scope},
            "environment",
        )

    if client_type == "user":
        return None
    try:
        text = PANOPTO_LEGACY_KEY_PATH.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as err:
        raise RuntimeError("Unable to read legacy Panopto credentials") from err
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if len(lines) != 2:
        raise ValueError("Legacy Panopto credential file must contain a complete pair")
    credentials = _validated_panopto_credentials(
        {"client_id": lines[0], "client_secret": lines[1], "scope": scope},
        "legacy-file",
    )
    warnings.warn(
        "Using legacy Panopto credential file; migrate to the OS vault",
        RuntimeWarning,
        stacklevel=2,
    )
    return credentials


def store_panopto_client_credentials(
    credentials: PanoptoClientCredentials, client_type: str = "server"
) -> None:
    """Store an entire credential pair with one vault write, never print it."""
    credential_key = _panopto_credential_key(client_type)
    validated = _validated_panopto_credentials(
        {
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scope": credentials.scope,
        },
        "provided",
    )
    payload = json.dumps(
        {
            "client_id": validated.client_id,
            "client_secret": validated.client_secret,
            "scope": validated.scope,
        }
    )
    try:
        keyring.set_password(PANOPTO_KEYRING_SERVICE, credential_key, payload)
    except Exception:
        # Backend exception text can contain the value being stored.
        raise RuntimeError("Unable to store Panopto credentials in the OS vault") from None
