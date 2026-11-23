"""Secure storage and tiered resolution of the Canvas API token.

Ported from discussorator ``auth/credentials.py`` (the base), with the
resolution tiers COURSE_BUILDER had that discussorator lacked (its
``cantrips/auth.py`` and ``docs/auth.md``).

RESOLUTION ORDER (first hit wins):

1. System keyring - encrypted and protected by the operating system.
2. Environment variable - ``COURSEWRIGHT_API_TOKEN``, then the
   COURSE_BUILDER name ``CANVAS_API_TOKEN``.  Good for automation.
3. ``api.key`` in the base directory - plain text, logs a warning.
4. ``../api.key`` beside the base directory - COURSE_BUILDER's legacy
   location, plain text, logs the same warning.

SECRETS NEVER LEAVE UNMASKED: every function that hands a token back
wraps it in :class:`pydantic.SecretStr`, whose ``repr`` and ``str`` are
masked.  The one deliberate place the value is revealed is the caller
that builds the HTTP ``Authorization`` header, through
``token.get_secret_value()``.  Nothing here logs a token.

This module is library code (AGENTS.md 3.8): it returns data and
messages and logs through ``logging``; it never prints or prompts.
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import SecretStr

logger = logging.getLogger(__name__)

# Other tools on the same machine keep the same Canvas token under
# their own names.  The audit in ``list_token_sources`` looks at these
# so the instructor can see which copy exists where; ordinary
# resolution (``get_token``) does not read the sibling vault entries.
SIBLING_KEYRING_ENTRIES: tuple[tuple[str, str], ...] = (
    ("discussorator", "canvas_api_token"),
    ("canvas-course-builder", "canvas_api_token"),
)
SIBLING_ENVIRONMENT_VARIABLES: tuple[str, ...] = ("DISCUSSORATOR_API_TOKEN",)


class StorageMethod(StrEnum):
    """
    Credential storage methods, ordered by security preference.

    WHY THESE METHODS:
    - KEYRING: Most secure - encrypted, OS-protected
    - ENVIRONMENT: Secure for automation - not persistent
    - FILE: Least secure - plain text on disk
    """

    KEYRING = "keyring"
    ENVIRONMENT = "environment"
    FILE = "file"
    NONE = "none"


@dataclass(frozen=True)
class TokenSource:
    """
    One place a Canvas token could live, and whether one was found.

    The token itself stays inside a ``SecretStr`` so printing or
    logging a ``TokenSource`` can never reveal it.  ``token_length``
    is what a status screen may show instead of the value.
    """

    kind: StorageMethod
    label: str
    token: SecretStr | None

    @property
    def is_present(self) -> bool:
        """Return True when this source currently holds a token."""
        return self.token is not None

    @property
    def token_length(self) -> int:
        """Return the token's length, or 0 when the source is empty."""
        if self.token is None:
            return 0
        return len(self.token.get_secret_value())


def _wrap_secret(raw_value: str | None) -> SecretStr | None:
    """Wrap a non-empty raw string in ``SecretStr``; pass None through."""
    if not raw_value:
        return None
    return SecretStr(raw_value)


class CredentialManager:
    """
    Manages Canvas API credentials securely.

    DESIGN PHILOSOPHY:
    - Secure by default (keyring)
    - Backward compatible (api.key fallback, legacy ../api.key)
    - Clear warnings for insecure methods
    - Easy migration path

    Usage:
        manager = CredentialManager()

        # Store securely
        manager.store_token("my_token", use_keyring=True)

        # Retrieve securely (a masked SecretStr, or None)
        token = manager.get_token()

        # Check storage method
        method = manager.get_storage_method()
    """

    SERVICE_NAME = "coursewright"
    KEY_NAME = "canvas_api_token"
    FILE_NAME = "api.key"
    ENV_VAR_NAME = "COURSEWRIGHT_API_TOKEN"
    # COURSE_BUILDER's environment tier used this name; it is accepted
    # after ENV_VAR_NAME so existing shells keep working.
    FALLBACK_ENV_VAR_NAMES: tuple[str, ...] = ("CANVAS_API_TOKEN",)

    def __init__(
        self,
        base_path: Path | None = None,
        legacy_file_path: Path | None = None,
    ):
        """
        Initialize credential manager.

        Args:
            base_path: Base directory for file-based storage (defaults
                to current dir).
            legacy_file_path: COURSE_BUILDER's legacy token file.
                Defaults to ``api.key`` in the parent of ``base_path``
                (its documented ``../api.key``).
        """
        self.base_path = base_path or Path.cwd()
        self.file_path = self.base_path / self.FILE_NAME
        self.legacy_file_path = (
            legacy_file_path
            if legacy_file_path is not None
            else self.base_path.parent / self.FILE_NAME
        )
        self._keyring_available = self._check_keyring_available()

    def _check_keyring_available(self) -> bool:
        """
        Check if keyring is available and working.

        WHY: Keyring might not be available in some environments
        (Docker, CI/CD, etc.).  We need to detect this and fall back
        gracefully.

        Returns:
            True if keyring is available, False otherwise
        """
        try:
            import keyring

            # Try to access keyring to ensure it's actually working.
            # Some systems have keyring installed but no backend configured.
            keyring.get_keyring()
            return True
        except Exception as keyring_error:
            logger.debug(f"Keyring not available: {keyring_error}")
            return False

    # ------------------------------------------------------------------
    # Storing
    # ------------------------------------------------------------------

    def store_token(self, token: str | SecretStr, use_keyring: bool = True) -> tuple[bool, str]:
        """
        Store API token using the specified method.

        Args:
            token: Canvas API token to store (plain or ``SecretStr``)
            use_keyring: If True, use system keyring (secure). If False, use file.

        Returns:
            Tuple of (success: bool, message: str).  The message never
            contains the token.
        """
        raw_token = token.get_secret_value() if isinstance(token, SecretStr) else token
        if not raw_token or not raw_token.strip():
            return False, "Token cannot be empty"

        raw_token = raw_token.strip()

        if use_keyring and self._keyring_available:
            return self._store_in_keyring(raw_token)
        return self._store_in_file(raw_token)

    def _store_in_keyring(self, token: str) -> tuple[bool, str]:
        """
        Store token in system keyring (SECURE).

        HOW IT WORKS:
        1. Import keyring library
        2. Store token with service + key name
        3. OS encrypts and stores securely
        4. Requires OS password to access

        Returns:
            (True, success_message) or (False, error_message)
        """
        try:
            import keyring

            keyring.set_password(self.SERVICE_NAME, self.KEY_NAME, token)

            logger.info("✅ API token stored securely in system keyring")
            return True, "API token stored securely in system keyring"

        except Exception as keyring_error:
            logger.error(f"Failed to store token in keyring: {keyring_error}")
            return False, f"Failed to store in keyring: {keyring_error}"

    def _store_in_file(self, token: str) -> tuple[bool, str]:
        """
        Store token in file (INSECURE - NOT RECOMMENDED).

        SECURITY NOTES:
        - Sets file permissions to 600 (read/write for owner only)
        - Still visible in plain text
        - Still accessible to any process running as your user
        - Survives reboots and backups

        Returns:
            (True, warning_message) or (False, error_message)
        """
        try:
            self.file_path.write_text(token)

            # Set restrictive permissions (Unix only)
            # 0o600 = rw------- (owner can read/write, nobody else can access)
            with contextlib.suppress(OSError, AttributeError):
                os.chmod(self.file_path, 0o600)

            logger.warning(f"⚠️  Token stored in {self.FILE_NAME} (INSECURE)")
            return (
                True,
                f"⚠️  Token stored in {self.FILE_NAME} "
                "(not recommended - use keyring for security)",
            )

        except Exception as file_error:
            logger.error(f"Failed to store token in file: {file_error}")
            return False, f"Failed to store in file: {file_error}"

    # ------------------------------------------------------------------
    # Resolving
    # ------------------------------------------------------------------

    def get_token(self) -> SecretStr | None:
        """
        Retrieve API token from any available source.

        PRIORITY ORDER:
        1. System Keyring (most secure)
        2. Environment Variable (for automation)
        3. api.key File (least secure, logs a warning)
        4. Legacy ../api.key File (COURSE_BUILDER, logs a warning)

        WHY THIS ORDER:
        - Keyring is most secure, check first
        - Env vars are good for CI/CD
        - Files are the fallback for compatibility

        Returns:
            The token as a masked ``SecretStr``, or None if not found.
            Call ``.get_secret_value()`` only where the request header
            is built.
        """
        # Try keyring first (most secure)
        if self._keyring_available:
            token = self._get_from_keyring()
            if token:
                return token

        # Try environment variable (good for automation)
        token = self._get_from_environment()
        if token:
            return token

        # Fall back to the files (log a security warning)
        for read_from_file in (self._get_from_file, self._get_from_legacy_file):
            token = read_from_file()
            if token:
                self._show_security_warning()
                return token

        # No token found
        return None

    def _get_from_keyring(self) -> SecretStr | None:
        """Retrieve token from system keyring, or None if not found."""
        try:
            import keyring

            token = keyring.get_password(self.SERVICE_NAME, self.KEY_NAME)
            if token:
                logger.debug("Retrieved token from keyring")
            return _wrap_secret(token)
        except Exception as keyring_error:
            logger.debug(f"Could not retrieve from keyring: {keyring_error}")
            return None

    def _environment_variable_names(self) -> tuple[str, ...]:
        """Return every environment variable name resolution accepts, in order."""
        return (self.ENV_VAR_NAME, *self.FALLBACK_ENV_VAR_NAMES)

    def _get_from_environment(self) -> SecretStr | None:
        """
        Retrieve token from environment variable.

        WHY ENVIRONMENT VARIABLES:
        - Good for CI/CD pipelines
        - Good for Docker containers
        - Not persistent (cleared on logout)
        - Better than files, not as good as keyring

        Returns:
            Token or None if not found
        """
        for variable_name in self._environment_variable_names():
            token = os.getenv(variable_name)
            if token:
                logger.debug(f"Retrieved token from environment variable {variable_name}")
                return _wrap_secret(token)
        return None

    def _read_token_file(self, token_file_path: Path) -> SecretStr | None:
        """Read and strip one plain-text token file; None if absent or empty."""
        try:
            if token_file_path.exists():
                token = token_file_path.read_text().strip()
                if token:
                    logger.debug(f"Retrieved token from {token_file_path.name}")
                    return _wrap_secret(token)
        except Exception as file_error:
            logger.error(f"Error reading {token_file_path.name}: {file_error}")

        return None

    def _get_from_file(self) -> SecretStr | None:
        """Retrieve token from the api.key file (INSECURE)."""
        return self._read_token_file(self.file_path)

    def _get_from_legacy_file(self) -> SecretStr | None:
        """Retrieve token from COURSE_BUILDER's legacy ../api.key (INSECURE)."""
        return self._read_token_file(self.legacy_file_path)

    def _show_security_warning(self) -> None:
        """
        Log a warning when using insecure file-based storage.

        WHY LOG WARNINGS:
        - Users should know they're using insecure storage
        - Encourages migration to keyring
        - Meets security disclosure requirements
        """
        logger.warning("")
        logger.warning("=" * 70)
        logger.warning("⚠️  SECURITY WARNING: Using insecure file-based API token storage!")
        logger.warning("⚠️  Your API token is stored in plain text in 'api.key'")
        logger.warning("")
        logger.warning("🔐 Recommended: Migrate to secure keyring storage:")
        logger.warning("   $ coursewright auth migrate-to-keyring")
        logger.warning("=" * 70)
        logger.warning("")

    def get_storage_method(self) -> StorageMethod:
        """
        Determine which storage method is currently being used.

        WHY THIS IS USEFUL:
        - Shows user how their token is stored
        - Used by 'auth status' command
        - Helps identify security issues

        Returns:
            StorageMethod enum value
        """
        # Check keyring
        if self._keyring_available:
            try:
                import keyring

                if keyring.get_password(self.SERVICE_NAME, self.KEY_NAME):
                    return StorageMethod.KEYRING
            except Exception as keyring_error:
                logger.debug(f"Could not read keyring for status: {keyring_error}")

        # Check environment variable
        if any(os.getenv(name) for name in self._environment_variable_names()):
            return StorageMethod.ENVIRONMENT

        # Check files
        if self.file_path.exists() or self.legacy_file_path.exists():
            return StorageMethod.FILE

        # No token found
        return StorageMethod.NONE

    def list_token_sources(self) -> list[TokenSource]:
        """
        Audit every known place a Canvas token could live.

        Ported from COURSE_BUILDER ``gather_token_sources``: besides
        this project's own tiers it looks at the vault entries and
        environment variable that discussorator and COURSE_BUILDER
        used, to answer "which of my copies of this token exists
        where?".  Tokens come back masked; show ``token_length``.

        Returns:
            One ``TokenSource`` per candidate, in resolution order,
            sibling locations after this project's own.
        """
        keyring_entries = ((self.SERVICE_NAME, self.KEY_NAME), *SIBLING_KEYRING_ENTRIES)
        variable_names = (*self._environment_variable_names(), *SIBLING_ENVIRONMENT_VARIABLES)
        token_files = (self.file_path, self.legacy_file_path)

        sources = [
            TokenSource(
                StorageMethod.KEYRING,
                f"{service_name}/{key_name}",
                self._read_keyring_entry(service_name, key_name),
            )
            for service_name, key_name in keyring_entries
        ]
        sources.extend(
            TokenSource(
                StorageMethod.ENVIRONMENT,
                variable_name,
                _wrap_secret((os.getenv(variable_name) or "").strip()),
            )
            for variable_name in variable_names
        )
        sources.extend(
            TokenSource(
                StorageMethod.FILE,
                str(token_file_path),
                self._read_token_file(token_file_path),
            )
            for token_file_path in token_files
        )
        return sources

    def _read_keyring_entry(self, service_name: str, key_name: str) -> SecretStr | None:
        """Read one vault entry for the audit; None if absent or unreadable."""
        if not self._keyring_available:
            return None
        try:
            import keyring

            return _wrap_secret((keyring.get_password(service_name, key_name) or "").strip())
        except Exception as keyring_error:
            logger.debug(f"Could not read {service_name}/{key_name}: {keyring_error}")
            return None

    # ------------------------------------------------------------------
    # Removing and migrating
    # ------------------------------------------------------------------

    def delete_token(self, delete_file: bool = True) -> tuple[bool, str]:
        """
        Remove stored token from the keyring and the api.key file.

        The legacy ``../api.key`` lives outside this project's
        directory and is never deleted here; remove it by hand.

        Args:
            delete_file: If True, also delete api.key file

        Returns:
            Tuple of (success: bool, message: str)
        """
        messages = []
        overall_success = True

        # Remove from keyring
        if self._keyring_available:
            try:
                import keyring

                keyring.delete_password(self.SERVICE_NAME, self.KEY_NAME)
                messages.append("✅ Removed token from keyring")
            except Exception as keyring_error:
                if "not found" not in str(keyring_error).lower():
                    messages.append(f"⚠️  Could not remove from keyring: {keyring_error}")
                    overall_success = False

        # Remove file if requested
        if delete_file and self.file_path.exists():
            try:
                self.file_path.unlink()
                messages.append(f"✅ Deleted {self.FILE_NAME}")
            except Exception as file_error:
                messages.append(f"❌ Could not delete {self.FILE_NAME}: {file_error}")
                overall_success = False

        if not messages:
            messages.append("ℹ️  No stored token found")

        return overall_success, "\n".join(messages)

    def migrate_to_keyring(self) -> tuple[bool, str]:
        """
        Migrate from file-based storage to secure keyring.

        MIGRATION PROCESS:
        1. Find api.key (or, failing that, the legacy ../api.key)
        2. Read token from file
        3. Store in keyring
        4. Verify keyring storage worked

        The file is left in place; delete it once the keyring entry
        is confirmed working.

        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self._keyring_available:
            return False, "❌ Keyring is not available on this system"

        if self.file_path.exists():
            source_file_path = self.file_path
        elif self.legacy_file_path.exists():
            source_file_path = self.legacy_file_path
        else:
            return False, f"❌ No {self.FILE_NAME} file found to migrate"

        try:
            # Read token from file
            token = source_file_path.read_text().strip()
            if not token:
                return False, f"❌ {self.FILE_NAME} is empty"

            # Store in keyring
            success, message = self._store_in_keyring(token)
            if not success:
                return False, f"❌ Failed to store in keyring: {message}"

            # Verify it worked
            if not self._get_from_keyring():
                return False, "❌ Failed to verify keyring storage"

            return True, "✅ Token migrated to keyring successfully!"

        except Exception as migration_error:
            return False, f"❌ Migration failed: {migration_error}"

    def is_keyring_available(self) -> bool:
        """
        Check if keyring is available on this system.

        Returns:
            True if keyring is available
        """
        return self._keyring_available


# =============================================================================
# Convenience Functions
# =============================================================================

_default_manager: CredentialManager | None = None


def get_credential_manager() -> CredentialManager:
    """
    Get the default credential manager instance (singleton pattern).

    WHY SINGLETON:
    - One manager per application
    - Consistent behavior across modules
    - Reduced object creation overhead

    Returns:
        CredentialManager instance
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = CredentialManager()
    return _default_manager


def get_api_token() -> SecretStr | None:
    """
    Convenience function to get the API token.

    Returns:
        The token as a masked ``SecretStr``, or None.
    """
    return get_credential_manager().get_token()


def store_api_token(token: str | SecretStr, use_keyring: bool = True) -> tuple[bool, str]:
    """
    Convenience function to store API token.

    Args:
        token: API token to store
        use_keyring: Use keyring (True) or file (False)

    Returns:
        (success, message) tuple
    """
    return get_credential_manager().store_token(token, use_keyring)
