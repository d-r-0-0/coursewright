"""Secure storage of a Canvas browser session (cookies and CSRF token).

Ported from discussorator ``auth/credentials.py``
(``BrowserSessionManager``), split into its own module to keep files
small.

DESIGN PHILOSOPHY:
- Mirrors ``CredentialManager`` but stores a serialized session blob
  (cookies + csrf_token) instead of a plain API token.
- Keyring is always tried first (tier 1 - encrypted, OS-protected).
- If keyring is unavailable or refuses the write, fall back to a JSON
  file in ``base_path``.  Session cookies expire quickly, so the
  plain-text risk window is small.

LIBRARY CODE IS SILENT (AGENTS.md 3.8): discussorator printed a Rich
warning when the file fallback was used.  Here the same warning is
logged and carried in the returned message; ``ui/`` decides how to
show it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

from coursewright.core.auth.credentials import StorageMethod

logger = logging.getLogger(__name__)


class SessionBlob(dict):
    """
    A session blob dict whose ``repr`` and ``str`` never show secrets.

    STORED BLOB FORMAT:
    {
        "cookies": [{"name": "...", "value": "...", ...}, ...],
        "csrf_token": "<decoded CSRF string>"
    }

    WHY A DICT SUBCLASS: callers index it, compare it and serialise
    it exactly as discussorator's plain dict, but a stray log line,
    f-string or debugger display shows only the cookie count.
    """

    def __repr__(self) -> str:
        cookies = self.get("cookies")
        cookie_count = len(cookies) if isinstance(cookies, list) else 0
        csrf_state = "set" if self.get("csrf_token") else "absent"
        return f"SessionBlob(cookies={cookie_count}, csrf_token=<{csrf_state}>)"

    __str__ = __repr__


class BrowserSessionManager:
    """
    Manages Canvas browser session cookies and CSRF token securely.

    Usage:
        manager = BrowserSessionManager()

        # Store the blob harvested by the browser login
        manager.store_session({"cookies": [...], "csrf_token": "..."})

        # Retrieve it later
        blob = manager.get_session()
        if blob:
            cookies = blob["cookies"]
            csrf = blob["csrf_token"]

        # Check where it is stored
        method = manager.get_storage_method()
    """

    # The same keyring service name as CredentialManager, so all
    # Coursewright secrets live in one keyring namespace.
    SERVICE_NAME = "coursewright"

    # Unique keyring key name - must differ from CredentialManager's
    # "canvas_api_token" key so the two don't overwrite each other.
    KEY_NAME = "canvas_browser_session"

    # Plaintext fallback file name.
    FILE_NAME = ".canvas_session.json"

    def __init__(self, base_path: Path | None = None):
        """
        Initialise the session manager.

        Args:
            base_path: Directory for the file fallback. Defaults to
                       the user's private application state directory. Tests pass
                       tmp_path here to avoid polluting the workspace.
        """
        state_root = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get(
            "XDG_STATE_HOME"
        )
        self.base_path = base_path or (
            Path(state_root).expanduser() if state_root else Path.home() / ".local" / "state"
        ) / "coursewright"
        self.file_path = self.base_path / self.FILE_NAME
        self._keyring_available = self._check_keyring_available()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_keyring_available(self) -> bool:
        """
        Return True if the system keyring backend is usable.

        WHY WE CHECK AT INIT:
        Keyring may be installed but have no usable backend in headless
        environments (Docker, CI).  Probing once at init avoids repeated
        failures at call-time.
        """
        try:
            import keyring

            keyring.get_keyring()
            return True
        except Exception as keyring_error:
            logger.debug(f"Keyring not available for session manager: {keyring_error}")
            return False

    def _serialize_blob(self, session_blob: dict) -> str:
        """
        Serialise the session blob to a compact JSON string for storage.

        WHY JSON:
        Keyring stores arbitrary strings; JSON round-trips dicts without
        data loss and is human-readable for debugging the file fallback.
        """
        return json.dumps(session_blob, separators=(",", ":"))

    def _deserialize_blob(self, raw_json: str) -> SessionBlob | None:
        """
        Deserialise a JSON string back to a session blob.

        Returns None (not raises) on parse failure so callers can treat
        a corrupt stored value the same as a missing one.
        """
        try:
            parsed = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError) as parse_error:
            # Only the error kind and position are logged: the message
            # of a JSONDecodeError does not include the document.
            logger.warning(f"Failed to parse stored session blob: {parse_error}")
            return None
        return SessionBlob(parsed) if isinstance(parsed, dict) else parsed

    def _file_fallback_warning(self) -> str:
        """Return the warning text used whenever the file fallback is taken."""
        return (
            "⚠️  Keyring unavailable -- browser session saved to "
            f"{self.file_path} (plain text). "
            "Keep this file out of version control; it will expire quickly."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store_session(self, session_blob: dict) -> tuple[bool, str]:
        """
        Persist a Canvas browser session blob.

        Tries keyring first.  Falls back to a JSON file (with a logged
        warning, repeated in the returned message) if keyring is
        unavailable or rejects the write.

        Args:
            session_blob: Dict containing "cookies" (list) and
                          "csrf_token" (str).

        Returns:
            (success, human_readable_message) tuple.
        """
        if not session_blob:
            return False, "Session blob cannot be empty"

        serialized = self._serialize_blob(session_blob)

        if self._keyring_available:
            keyring_success, keyring_message = self._store_in_keyring(serialized)
            if keyring_success:
                return keyring_success, keyring_message

            # Keyring write failed - most commonly because a browser session
            # blob (cookies + CSRF) exceeds the OS credential-store size limit.
            # On Windows the Credential Manager caps a single credential blob
            # at ~2560 bytes and rejects larger writes with
            # ``(1783, 'CredWrite', 'The stub received bad data.')``.  Because
            # browser sessions are routinely larger than that, we fall back to
            # the JSON file rather than failing the whole login.
            logger.warning(
                f"Keyring write failed ({keyring_message}); "
                "falling back to file storage for the browser session."
            )

        logger.warning(self._file_fallback_warning())
        return self._store_in_file(serialized)

    def get_session(self) -> SessionBlob | None:
        """
        Retrieve the stored Canvas browser session blob.

        Checks keyring first, then falls back to the JSON file.

        Returns:
            Session blob (a dict with a masked ``repr``) or None if
            nothing is stored.
        """
        # Try keyring first
        if self._keyring_available:
            raw = self._get_from_keyring()
            if raw:
                return self._deserialize_blob(raw)

        # Fall back to file
        raw = self._get_from_file()
        if raw:
            return self._deserialize_blob(raw)

        return None

    def delete_session(self) -> tuple[bool, str]:
        """
        Remove the stored session from all locations (keyring + file).

        Returns:
            (overall_success, human_readable_message) tuple.
        """
        messages: list[str] = []
        overall_success = True

        # Remove from keyring if available
        if self._keyring_available:
            try:
                import keyring

                keyring.delete_password(self.SERVICE_NAME, self.KEY_NAME)
                messages.append("✅ Removed browser session from keyring")
            except Exception as keyring_error:
                if "not found" not in str(keyring_error).lower():
                    messages.append(f"⚠️  Could not remove session from keyring: {keyring_error}")
                    overall_success = False

        # Remove file if it exists
        if self.file_path.exists():
            try:
                self.file_path.unlink()
                messages.append(f"✅ Deleted {self.FILE_NAME}")
            except OSError as file_error:
                messages.append(f"❌ Could not delete {self.FILE_NAME}: {file_error}")
                overall_success = False

        if not messages:
            messages.append("ℹ️  No stored browser session found")

        return overall_success, "\n".join(messages)

    def get_storage_method(self) -> StorageMethod:
        """
        Report which storage tier currently holds the session.

        Returns:
            StorageMethod.KEYRING  - session is in OS keyring
            StorageMethod.FILE     - session is in .canvas_session.json
            StorageMethod.NONE     - no session stored anywhere
        """
        # Check keyring first
        if self._keyring_available:
            try:
                import keyring

                if keyring.get_password(self.SERVICE_NAME, self.KEY_NAME):
                    return StorageMethod.KEYRING
            except Exception as keyring_error:
                logger.debug(f"Could not read keyring for status: {keyring_error}")

        # Check file fallback
        if self.file_path.exists():
            return StorageMethod.FILE

        return StorageMethod.NONE

    def is_keyring_available(self) -> bool:
        """
        Return True if the OS keyring is available on this system.

        Convenience accessor so callers don't access the private
        ``_keyring_available`` attribute directly.
        """
        return self._keyring_available

    # ------------------------------------------------------------------
    # Private storage primitives
    # ------------------------------------------------------------------

    def _store_in_keyring(self, serialized_json: str) -> tuple[bool, str]:
        """
        Write the serialised session blob to the OS keyring.

        The blob is stored as a keyring "password" string - keyring
        treats any string value as opaque, so JSON is perfectly valid.

        Returns:
            (True, success_message) or (False, error_message)
        """
        try:
            import keyring

            keyring.set_password(self.SERVICE_NAME, self.KEY_NAME, serialized_json)
            logger.info("✅ Browser session stored securely in system keyring")
            return True, "Browser session stored securely in system keyring"

        except Exception as keyring_error:
            logger.error(f"Failed to store session in keyring: {keyring_error}")
            return False, f"Failed to store session in keyring: {keyring_error}"

    def _store_in_file(self, serialized_json: str) -> tuple[bool, str]:
        """
        Write the serialised session blob to the JSON fallback file.

        SECURITY NOTES:
        - Sets file permissions to 0o600 (owner read/write only).
        - Session cookies expire quickly, limiting the plain-text risk.

        Returns:
            (True, warning_message) or (False, error_message)
        """
        temporary_path: Path | None = None
        try:
            self.base_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            # mkstemp creates the secret with 0600 permissions before any
            # bytes are written. Atomic replacement also avoids following
            # a stale fallback file's symlink or leaving partial JSON.
            descriptor, temporary_name = tempfile.mkstemp(dir=self.base_path)
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(serialized_json)
            temporary_path.replace(self.file_path)

            logger.warning(f"⚠️  Browser session stored in {self.FILE_NAME} (plain text)")
            return True, self._file_fallback_warning()

        except Exception as file_error:
            logger.error(f"Failed to store session in file: {file_error}")
            return False, f"Failed to store session in file: {file_error}"
        finally:
            if temporary_path is not None:
                with contextlib.suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    def _get_from_keyring(self) -> str | None:
        """
        Read the raw JSON string from the OS keyring.

        Private: the raw string never leaves this class; ``get_session``
        hands callers a masked ``SessionBlob``.
        """
        try:
            import keyring

            value = keyring.get_password(self.SERVICE_NAME, self.KEY_NAME)
            if value:
                logger.debug("Retrieved browser session from keyring")
            return value
        except Exception as keyring_error:
            logger.debug(f"Could not retrieve session from keyring: {keyring_error}")
            return None

    def _get_from_file(self) -> str | None:
        """
        Read the raw JSON string from the file fallback.

        Returns:
            Raw JSON string, or None if the file is absent or empty.
        """
        try:
            if self.file_path.exists():
                content = self.file_path.read_text().strip()
                if content:
                    logger.debug(f"Retrieved browser session from {self.FILE_NAME}")
                    return content
        except OSError as file_error:
            logger.error(f"Error reading {self.FILE_NAME}: {file_error}")
        return None


_default_browser_session_manager: BrowserSessionManager | None = None


def get_browser_session_manager() -> BrowserSessionManager:
    """
    Return the default BrowserSessionManager singleton.

    WHY SINGLETON:
    Same reasoning as get_credential_manager - one manager per
    application avoids redundant keyring probes and ensures all
    callers share the same file path.
    """
    global _default_browser_session_manager
    if _default_browser_session_manager is None:
        _default_browser_session_manager = BrowserSessionManager()
    return _default_browser_session_manager
