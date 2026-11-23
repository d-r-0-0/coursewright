"""
Tests for Credential Manager (``core/auth/credentials.py``)
============================================================

Ported from discussorator ``tests/test_credentials.py``; sections 11-13
cover what the Coursewright port adds (COURSE_BUILDER's tiers, the
token-source audit, and masking).

PURPOSE:
--------
These tests verify the tiered credential resolution system that protects
Canvas API tokens.  Correct behaviour here is *security-critical*: a bug
could silently expose tokens in plain text or lose credentials entirely.

WHAT WE'RE TESTING:
-------------------
1. Token retrieval priority (keyring > env var > file > legacy file)
2. Keyring storage - store, retrieve, delete
3. Environment-variable retrieval
4. File-based storage - write, read, permissions, security warning
5. Empty / missing token edge cases
6. ``get_storage_method`` reporting
7. ``migrate_to_keyring`` flow (happy path + error paths)
8. ``delete_token`` cleanup
9. ``store_token`` with empty / whitespace-only input
10. Convenience helpers and fallback paths
11. COURSE_BUILDER tiers: ``CANVAS_API_TOKEN`` and legacy ``../api.key``
12. ``list_token_sources`` audit
13. Tokens are never shown by ``repr``, ``str`` or the log

WHY MOCK KEYRING:
-----------------
The ``keyring`` library talks to OS-level secret stores (GNOME Keyring,
macOS Keychain, Windows Credential Locker).  In CI and containers these
backends often don't exist, so we mock ``keyring`` entirely and test the
*logic* of our credential manager in isolation.

Every token below is synthetic.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from coursewright.core.auth import credentials as credentials_module
from coursewright.core.auth.credentials import (
    CredentialManager,
    StorageMethod,
    TokenSource,
    get_api_token,
    get_credential_manager,
    store_api_token,
)

TOKEN_ENVIRONMENT_VARIABLES = (
    "COURSEWRIGHT_API_TOKEN",
    "CANVAS_API_TOKEN",
    "DISCUSSORATOR_API_TOKEN",
)


def reveal(token: SecretStr | None) -> str | None:
    """Unwrap a resolved token for comparison; None stays None."""
    return None if token is None else token.get_secret_value()


# =============================================================================
# FIXTURES - a ``CredentialManager`` with a mocked keyring backend
# =============================================================================


@pytest.fixture(autouse=True)
def clean_token_environment(monkeypatch):
    """
    Remove every token environment variable before each test.

    WHY:  A developer's shell may export a real token; the tests must
    neither read it nor depend on it.
    """
    for variable_name in TOKEN_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable_name, raising=False)


@pytest.fixture
def project_directory(tmp_path):
    """
    A project directory nested one level down, so the legacy
    ``../api.key`` location is also inside the temporary directory.
    """
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


@pytest.fixture
def credential_manager(project_directory):
    """
    Create a ``CredentialManager`` whose keyring check returns True and
    whose file storage points at a temporary directory.

    WHY:  We need deterministic tests that don't touch the real OS
    keyring or leave ``api.key`` files scattered across the filesystem.
    """
    with patch.object(CredentialManager, "_check_keyring_available", return_value=True):
        manager = CredentialManager(base_path=project_directory)
    return manager


@pytest.fixture
def credential_manager_no_keyring(project_directory):
    """
    A ``CredentialManager`` where keyring is *not* available.

    WHY:  Tests the fallback path (env var > file) when running in
    environments without a keyring backend (Docker, headless CI, etc.).
    """
    with patch.object(CredentialManager, "_check_keyring_available", return_value=False):
        manager = CredentialManager(base_path=project_directory)
    return manager


# =============================================================================
# 1.  TOKEN RETRIEVAL PRIORITY
# =============================================================================


class TestGetTokenPriority:
    """Verify the keyring > env > file priority order."""

    def test_keyring_is_checked_first(self, credential_manager):
        """If keyring has a token, env and file are never consulted."""
        with patch("keyring.get_password", return_value="keyring-token"):
            token = credential_manager.get_token()

        assert reveal(token) == "keyring-token"

    def test_env_var_used_when_keyring_empty(self, credential_manager, monkeypatch):
        """When keyring returns None, the environment variable is tried."""
        monkeypatch.setenv("COURSEWRIGHT_API_TOKEN", "env-token")
        with patch("keyring.get_password", return_value=None):
            token = credential_manager.get_token()

        assert reveal(token) == "env-token"

    def test_file_used_as_last_resort(self, credential_manager):
        """When keyring and env are both empty, api.key file is read."""
        credential_manager.file_path.write_text("file-token")

        with patch("keyring.get_password", return_value=None):
            token = credential_manager.get_token()

        assert reveal(token) == "file-token"

    def test_returns_none_when_nothing_found(self, credential_manager):
        """No keyring, no env, no file -> None."""
        with patch("keyring.get_password", return_value=None):
            token = credential_manager.get_token()

        assert token is None


# =============================================================================
# 2.  KEYRING STORAGE
# =============================================================================


class TestKeyringStorage:
    """Verify store/retrieve/delete via keyring."""

    def test_store_in_keyring_succeeds(self, credential_manager):
        with patch("keyring.set_password") as mock_set:
            success, message = credential_manager.store_token("my-token", use_keyring=True)

        assert success is True
        mock_set.assert_called_once_with(
            "coursewright",
            "canvas_api_token",
            "my-token",
        )

    def test_store_in_keyring_handles_exception(self, credential_manager):
        with patch("keyring.set_password", side_effect=Exception("backend locked")):
            success, message = credential_manager.store_token("my-token", use_keyring=True)

        assert success is False
        assert "Failed" in message


# =============================================================================
# 3.  ENVIRONMENT VARIABLE RETRIEVAL
# =============================================================================


class TestEnvironmentRetrieval:
    """Verify ``_get_from_environment``."""

    def test_reads_from_env_var(self, credential_manager, monkeypatch):
        monkeypatch.setenv("COURSEWRIGHT_API_TOKEN", "env-token-123")

        token = credential_manager._get_from_environment()

        assert reveal(token) == "env-token-123"

    def test_returns_none_when_unset(self, credential_manager):
        token = credential_manager._get_from_environment()

        assert token is None


# =============================================================================
# 4.  FILE-BASED STORAGE
# =============================================================================


class TestFileStorage:
    """Verify file-based store and retrieve."""

    def test_store_in_file_creates_api_key(self, credential_manager_no_keyring):
        """Storing a token when keyring is unavailable writes to api.key."""
        success, message = credential_manager_no_keyring.store_token(
            "file-token",
            use_keyring=False,
        )

        assert success is True
        assert credential_manager_no_keyring.file_path.exists()
        assert credential_manager_no_keyring.file_path.read_text() == "file-token"

    def test_reads_token_from_file(self, credential_manager_no_keyring):
        credential_manager_no_keyring.file_path.write_text("  stored-token  \n")

        token = credential_manager_no_keyring._get_from_file()

        assert reveal(token) == "stored-token"

    def test_returns_none_for_missing_file(self, credential_manager_no_keyring):
        token = credential_manager_no_keyring._get_from_file()

        assert token is None

    def test_returns_none_for_empty_file(self, credential_manager_no_keyring):
        credential_manager_no_keyring.file_path.write_text("   \n")

        token = credential_manager_no_keyring._get_from_file()

        assert token is None

    def test_reading_from_file_logs_security_warning(
        self, credential_manager_no_keyring, caplog
    ):
        """Resolving from plain-text storage warns through the log, not stdout."""
        credential_manager_no_keyring.file_path.write_text("file-token")

        with caplog.at_level(logging.WARNING):
            credential_manager_no_keyring.get_token()

        assert "SECURITY WARNING" in caplog.text
        assert "coursewright auth migrate-to-keyring" in caplog.text


# =============================================================================
# 5.  EMPTY / WHITESPACE TOKEN HANDLING
# =============================================================================


class TestEmptyTokenHandling:
    """Verify that empty or whitespace-only tokens are rejected."""

    def test_empty_string_rejected(self, credential_manager):
        success, message = credential_manager.store_token("")

        assert success is False
        assert "empty" in message.lower()

    def test_whitespace_only_rejected(self, credential_manager):
        success, message = credential_manager.store_token("   \t  ")

        assert success is False
        assert "empty" in message.lower()

    def test_strips_whitespace_before_storing(self, credential_manager):
        """Leading/trailing whitespace is stripped before keyring storage."""
        with patch("keyring.set_password") as mock_set:
            credential_manager.store_token("  real-token  ", use_keyring=True)

        mock_set.assert_called_once_with(
            "coursewright",
            "canvas_api_token",
            "real-token",
        )

    def test_accepts_a_secret_str(self, credential_manager):
        """A token that is already masked can be stored without unwrapping."""
        with patch("keyring.set_password") as mock_set:
            success, _message = credential_manager.store_token(SecretStr(" wrapped-token "))

        assert success is True
        mock_set.assert_called_once_with("coursewright", "canvas_api_token", "wrapped-token")


# =============================================================================
# 6.  ``get_storage_method`` REPORTING
# =============================================================================


class TestGetStorageMethod:
    """Verify ``get_storage_method`` returns the correct enum value."""

    def test_reports_keyring_when_token_in_keyring(self, credential_manager):
        with patch("keyring.get_password", return_value="some-token"):
            method = credential_manager.get_storage_method()

        assert method == StorageMethod.KEYRING

    def test_reports_environment_when_env_set(self, credential_manager, monkeypatch):
        monkeypatch.setenv("COURSEWRIGHT_API_TOKEN", "env-tok")
        with patch("keyring.get_password", return_value=None):
            method = credential_manager.get_storage_method()

        assert method == StorageMethod.ENVIRONMENT

    def test_reports_file_when_api_key_exists(self, credential_manager):
        credential_manager.file_path.write_text("file-tok")

        with patch("keyring.get_password", return_value=None):
            method = credential_manager.get_storage_method()

        assert method == StorageMethod.FILE

    def test_reports_none_when_nothing_stored(self, credential_manager):
        with patch("keyring.get_password", return_value=None):
            method = credential_manager.get_storage_method()

        assert method == StorageMethod.NONE


# =============================================================================
# 7.  MIGRATE TO KEYRING
# =============================================================================


class TestMigrateToKeyring:
    """Verify the file -> keyring migration workflow."""

    def test_successful_migration(self, credential_manager):
        """Happy path: file exists, keyring store succeeds, verify succeeds."""
        credential_manager.file_path.write_text("migrate-me")

        with (
            patch("keyring.set_password"),
            patch("keyring.get_password", return_value="migrate-me"),
        ):
            success, message = credential_manager.migrate_to_keyring()

        assert success is True
        assert "successfully" in message.lower()

    def test_fails_when_keyring_unavailable(self, credential_manager_no_keyring):
        success, message = credential_manager_no_keyring.migrate_to_keyring()

        assert success is False
        assert "not available" in message.lower()

    def test_fails_when_no_file_exists(self, credential_manager):
        success, message = credential_manager.migrate_to_keyring()

        assert success is False
        assert "no" in message.lower()

    def test_fails_when_file_is_empty(self, credential_manager):
        credential_manager.file_path.write_text("  \n")

        success, message = credential_manager.migrate_to_keyring()

        assert success is False
        assert "empty" in message.lower()

    def test_fails_when_keyring_verification_fails(self, credential_manager):
        """A write that cannot be read back is reported, not assumed good."""
        credential_manager.file_path.write_text("migrate-me")

        with (
            patch("keyring.set_password"),
            patch("keyring.get_password", return_value=None),
        ):
            success, message = credential_manager.migrate_to_keyring()

        assert success is False
        assert "verify" in message.lower()


# =============================================================================
# 8.  DELETE TOKEN
# =============================================================================


class TestDeleteToken:
    """Verify ``delete_token`` removes credentials from all locations."""

    def test_deletes_from_keyring_and_file(self, credential_manager):
        """Both keyring and file are cleaned up."""
        credential_manager.file_path.write_text("old-token")

        with patch("keyring.delete_password") as mock_delete:
            success, message = credential_manager.delete_token(delete_file=True)

        assert success is True
        mock_delete.assert_called_once()
        assert not credential_manager.file_path.exists()

    def test_keeps_file_when_delete_file_false(self, credential_manager):
        credential_manager.file_path.write_text("keep-me")

        with patch("keyring.delete_password"):
            success, _message = credential_manager.delete_token(delete_file=False)

        assert success is True
        assert credential_manager.file_path.exists()

    def test_reports_nothing_found_without_keyring_or_file(self, credential_manager_no_keyring):
        success, message = credential_manager_no_keyring.delete_token()

        assert success is True
        assert "no stored token" in message.lower()


# =============================================================================
# 9.  ``is_keyring_available``
# =============================================================================


class TestIsKeyringAvailable:
    """Verify the keyring availability flag."""

    def test_reports_true_when_available(self, credential_manager):
        assert credential_manager.is_keyring_available() is True

    def test_reports_false_when_unavailable(self, credential_manager_no_keyring):
        assert credential_manager_no_keyring.is_keyring_available() is False


# =============================================================================
# 10.  FALLBACK WITHOUT KEYRING, AND THE CONVENIENCE HELPERS
# =============================================================================


class TestNoKeyringFallback:
    """Verify that the manager works correctly when keyring is absent."""

    def test_get_token_falls_through_to_env(self, credential_manager_no_keyring, monkeypatch):
        """Without keyring, env var is the first source checked."""
        monkeypatch.setenv("COURSEWRIGHT_API_TOKEN", "fallback-env")

        token = credential_manager_no_keyring.get_token()

        assert reveal(token) == "fallback-env"

    def test_store_token_falls_through_to_file(self, credential_manager_no_keyring):
        """Without keyring, ``store_token(use_keyring=True)`` writes to file."""
        success, _msg = credential_manager_no_keyring.store_token(
            "fallback-file",
            use_keyring=True,
        )

        assert success is True
        assert credential_manager_no_keyring.file_path.read_text() == "fallback-file"


class TestConvenienceHelpers:
    """Verify the module-level singleton and its two shortcuts."""

    @pytest.fixture(autouse=True)
    def default_manager(self, credential_manager_no_keyring, monkeypatch):
        """Install a temporary-directory manager as the singleton."""
        monkeypatch.setattr(credentials_module, "_default_manager", credential_manager_no_keyring)
        return credential_manager_no_keyring

    def test_singleton_is_reused(self, default_manager):
        assert get_credential_manager() is default_manager
        assert get_credential_manager() is get_credential_manager()

    def test_singleton_is_created_on_first_use(self, monkeypatch):
        monkeypatch.setattr(credentials_module, "_default_manager", None)

        assert isinstance(get_credential_manager(), CredentialManager)

    def test_store_then_get_round_trip(self):
        success, _message = store_api_token("helper-token", use_keyring=False)

        assert success is True
        assert reveal(get_api_token()) == "helper-token"


# =============================================================================
# 11.  COURSE_BUILDER TIERS
# =============================================================================


class TestCourseBuilderTiers:
    """The tiers COURSE_BUILDER had that discussorator lacked."""

    def test_canvas_api_token_variable_is_accepted(
        self, credential_manager_no_keyring, monkeypatch
    ):
        monkeypatch.setenv("CANVAS_API_TOKEN", "builder-env-token")

        token = credential_manager_no_keyring.get_token()

        assert reveal(token) == "builder-env-token"
        assert credential_manager_no_keyring.get_storage_method() == StorageMethod.ENVIRONMENT

    def test_coursewright_variable_wins_over_canvas_api_token(
        self, credential_manager_no_keyring, monkeypatch
    ):
        monkeypatch.setenv("CANVAS_API_TOKEN", "builder-env-token")
        monkeypatch.setenv("COURSEWRIGHT_API_TOKEN", "coursewright-env-token")

        token = credential_manager_no_keyring.get_token()

        assert reveal(token) == "coursewright-env-token"

    def test_legacy_file_defaults_to_parent_directory(self, credential_manager_no_keyring):
        """COURSE_BUILDER kept its token one directory up: ``../api.key``."""
        expected_path = credential_manager_no_keyring.base_path.parent / "api.key"

        assert credential_manager_no_keyring.legacy_file_path == expected_path

    def test_legacy_parent_file_is_the_last_tier(self, credential_manager_no_keyring, caplog):
        credential_manager_no_keyring.legacy_file_path.write_text("legacy-token\n")

        with caplog.at_level(logging.WARNING):
            token = credential_manager_no_keyring.get_token()

        assert reveal(token) == "legacy-token"
        assert "SECURITY WARNING" in caplog.text
        assert credential_manager_no_keyring.get_storage_method() == StorageMethod.FILE

    def test_project_file_wins_over_legacy_parent_file(self, credential_manager_no_keyring):
        credential_manager_no_keyring.file_path.write_text("project-token")
        credential_manager_no_keyring.legacy_file_path.write_text("legacy-token")

        token = credential_manager_no_keyring.get_token()

        assert reveal(token) == "project-token"

    def test_legacy_file_path_can_be_configured(self, tmp_path):
        configured_path = tmp_path / "elsewhere" / "canvas.key"
        configured_path.parent.mkdir()
        configured_path.write_text("configured-token")

        with patch.object(CredentialManager, "_check_keyring_available", return_value=False):
            manager = CredentialManager(base_path=tmp_path, legacy_file_path=configured_path)

        assert reveal(manager.get_token()) == "configured-token"

    def test_migration_reads_the_legacy_file_and_leaves_it(self, credential_manager):
        credential_manager.legacy_file_path.write_text("legacy-token")

        with (
            patch("keyring.set_password") as mock_set,
            patch("keyring.get_password", return_value="legacy-token"),
        ):
            success, _message = credential_manager.migrate_to_keyring()

        assert success is True
        mock_set.assert_called_once_with("coursewright", "canvas_api_token", "legacy-token")
        assert credential_manager.legacy_file_path.exists()

    def test_delete_never_removes_the_legacy_file(self, credential_manager):
        credential_manager.legacy_file_path.write_text("legacy-token")

        with patch("keyring.delete_password"):
            credential_manager.delete_token(delete_file=True)

        assert credential_manager.legacy_file_path.exists()


# =============================================================================
# 12.  TOKEN SOURCE AUDIT
# =============================================================================


class TestListTokenSources:
    """``list_token_sources`` reports every candidate location, masked."""

    def test_lists_own_and_sibling_locations_in_order(self, credential_manager):
        with patch("keyring.get_password", return_value=None):
            sources = credential_manager.list_token_sources()

        assert [source.label for source in sources] == [
            "coursewright/canvas_api_token",
            "discussorator/canvas_api_token",
            "canvas-course-builder/canvas_api_token",
            "COURSEWRIGHT_API_TOKEN",
            "CANVAS_API_TOKEN",
            "DISCUSSORATOR_API_TOKEN",
            str(credential_manager.file_path),
            str(credential_manager.legacy_file_path),
        ]
        assert not any(source.is_present for source in sources)
        assert all(source.token_length == 0 for source in sources)

    def test_reports_presence_and_length_without_the_value(
        self, credential_manager, monkeypatch
    ):
        vault = {("discussorator", "canvas_api_token"): "  sibling-vault-token "}
        monkeypatch.setenv("DISCUSSORATOR_API_TOKEN", "sibling-env-token")
        credential_manager.file_path.write_text("file-token\n")

        with patch("keyring.get_password", side_effect=lambda *entry: vault.get(entry)):
            sources = credential_manager.list_token_sources()

        present = {source.label: source for source in sources if source.is_present}
        assert set(present) == {
            "discussorator/canvas_api_token",
            "DISCUSSORATOR_API_TOKEN",
            str(credential_manager.file_path),
        }
        vault_source = present["discussorator/canvas_api_token"]
        assert vault_source.kind == StorageMethod.KEYRING
        assert vault_source.token_length == len("sibling-vault-token")
        assert reveal(vault_source.token) == "sibling-vault-token"

    def test_skips_the_vault_when_keyring_is_unavailable(self, credential_manager_no_keyring):
        with patch("keyring.get_password") as mock_get:
            sources = credential_manager_no_keyring.list_token_sources()

        mock_get.assert_not_called()
        assert not any(source.is_present for source in sources)

    def test_a_failing_vault_read_is_reported_as_absent(self, credential_manager):
        with patch("keyring.get_password", side_effect=Exception("backend locked")):
            sources = credential_manager.list_token_sources()

        assert not any(source.is_present for source in sources)


# =============================================================================
# 13.  TOKENS ARE NEVER SHOWN
# =============================================================================


class TestTokensAreMasked:
    """Acceptance: no token value appears in a repr, a str or the log."""

    SYNTHETIC_TOKEN = "synthetic-canvas-token-0123456789"

    def test_resolved_token_repr_and_str_are_masked(self, credential_manager):
        with patch("keyring.get_password", return_value=self.SYNTHETIC_TOKEN):
            token = credential_manager.get_token()

        assert isinstance(token, SecretStr)
        assert self.SYNTHETIC_TOKEN not in repr(token)
        assert self.SYNTHETIC_TOKEN not in str(token)
        assert self.SYNTHETIC_TOKEN not in f"{token}"
        assert token.get_secret_value() == self.SYNTHETIC_TOKEN

    @pytest.mark.parametrize(
        "resolver_name",
        ["_get_from_keyring", "_get_from_environment", "_get_from_file", "_get_from_legacy_file"],
    )
    def test_every_tier_returns_a_masked_token(
        self, credential_manager, monkeypatch, resolver_name
    ):
        monkeypatch.setenv("COURSEWRIGHT_API_TOKEN", self.SYNTHETIC_TOKEN)
        credential_manager.file_path.write_text(self.SYNTHETIC_TOKEN)
        credential_manager.legacy_file_path.write_text(self.SYNTHETIC_TOKEN)

        with patch("keyring.get_password", return_value=self.SYNTHETIC_TOKEN):
            token = getattr(credential_manager, resolver_name)()

        assert isinstance(token, SecretStr)
        assert self.SYNTHETIC_TOKEN not in repr(token)

    def test_token_source_repr_is_masked(self):
        source = TokenSource(StorageMethod.FILE, "api.key", SecretStr(self.SYNTHETIC_TOKEN))

        assert self.SYNTHETIC_TOKEN not in repr(source)
        assert self.SYNTHETIC_TOKEN not in str(source)

    def test_manager_repr_does_not_hold_a_token(self, credential_manager):
        with patch("keyring.get_password", return_value=self.SYNTHETIC_TOKEN):
            credential_manager.get_token()

        assert self.SYNTHETIC_TOKEN not in repr(credential_manager)
        assert self.SYNTHETIC_TOKEN not in repr(vars(credential_manager))

    def test_messages_and_log_never_contain_the_token(self, credential_manager, caplog):
        """Store, resolve, audit, migrate and delete, with the log wide open."""
        vault: dict[tuple[str, str], str] = {}

        def set_password(service_name, key_name, value):
            vault[(service_name, key_name)] = value

        messages: list[str] = []
        with (
            caplog.at_level(logging.DEBUG),
            patch("keyring.set_password", side_effect=set_password),
            patch("keyring.get_password", side_effect=lambda *entry: vault.get(entry)),
            patch("keyring.delete_password"),
        ):
            messages.append(credential_manager.store_token(self.SYNTHETIC_TOKEN)[1])
            messages.append(
                credential_manager.store_token(self.SYNTHETIC_TOKEN, use_keyring=False)[1]
            )
            assert reveal(credential_manager.get_token()) == self.SYNTHETIC_TOKEN
            vault.clear()
            assert reveal(credential_manager.get_token()) == self.SYNTHETIC_TOKEN
            messages.append(repr(credential_manager.list_token_sources()))
            messages.append(credential_manager.migrate_to_keyring()[1])
            messages.append(str(credential_manager.get_storage_method()))
            messages.append(credential_manager.delete_token()[1])

        assert self.SYNTHETIC_TOKEN not in caplog.text
        assert self.SYNTHETIC_TOKEN not in "\n".join(messages)
