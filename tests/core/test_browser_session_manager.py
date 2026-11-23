"""
Tests for BrowserSessionManager (``core/auth/browser_session.py``)
===================================================================

Ported from discussorator ``tests/test_browser_session_manager.py``.

PURPOSE:
--------
These tests verify that BrowserSessionManager correctly stores, retrieves,
and deletes a Canvas browser session blob (cookies + csrf_token) using a
two-tier strategy:

  Tier 1 - System Keyring (mocked via ``patch("keyring.*")``)
  Tier 2 - Plain-text JSON file (``.canvas_session.json``) when keyring
           is unavailable.

WHAT WE'RE TESTING:
-------------------
1. Store / get / delete round-trip through keyring (mocked)
2. File fallback: blob written to .canvas_session.json when no keyring
3. File fallback reports a warning (returned and logged, never printed)
4. ``get_storage_method`` reports KEYRING / FILE / NONE correctly
5. Empty blob is rejected
6. Corrupt JSON stored in keyring / file returns None gracefully
7. ``delete_session`` cleans keyring AND file
8. ``is_keyring_available`` reflects the mocked state
9. The session blob's ``repr`` never shows cookie or CSRF values

WHY ALL KEYRING + FILE ACCESS IS MOCKED:
-----------------------------------------
The ``keyring`` library talks to OS-level secret stores that don't exist
in CI or containers.  File I/O is isolated to a pytest ``tmp_path`` so
tests leave no artefacts behind and can run in any order.

Every cookie and token below is synthetic.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from unittest.mock import patch

import pytest

from coursewright.core.auth import browser_session as browser_session_module
from coursewright.core.auth.browser_session import BrowserSessionManager, SessionBlob
from coursewright.core.auth.credentials import StorageMethod

# ---------------------------------------------------------------------------
# Sample session blobs used across multiple tests
# ---------------------------------------------------------------------------

SAMPLE_COOKIES = [
    {"name": "canvas_session", "value": "abc123", "domain": "canvas.example.edu"},
    {"name": "_legacy_normandy_session", "value": "xyz789", "domain": "canvas.example.edu"},
]

SAMPLE_BLOB = {
    "cookies": SAMPLE_COOKIES,
    "csrf_token": "csrf-token-value-here",
}

# Compact JSON representation that the manager serialises to before storing
SAMPLE_BLOB_JSON = json.dumps(SAMPLE_BLOB, separators=(",", ":"))


def test_default_fallback_uses_private_state_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state_root = tmp_path / "private-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("LOCALAPPDATA", str(state_root))
    # A legacy file in the working tree must not become the active secret.
    (tmp_path / BrowserSessionManager.FILE_NAME).write_text(SAMPLE_BLOB_JSON)
    monkeypatch.setattr(BrowserSessionManager, "_check_keyring_available", lambda self: False)
    manager = BrowserSessionManager()

    assert manager.get_session() is None
    success, message = manager.store_session(SAMPLE_BLOB)

    assert success, message
    assert manager.file_path == state_root / "coursewright" / manager.FILE_NAME
    assert manager.get_session() == SAMPLE_BLOB
    if os.name != "nt":
        assert stat.S_IMODE(manager.file_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(manager.base_path.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="Unix file permissions")
def test_fallback_replaces_existing_file_with_private_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(BrowserSessionManager, "_check_keyring_available", lambda self: False)
    manager = BrowserSessionManager(base_path=tmp_path)
    manager.file_path.write_text("old data")
    manager.file_path.chmod(0o644)

    success, message = manager.store_session(SAMPLE_BLOB)

    assert success, message
    assert stat.S_IMODE(manager.file_path.stat().st_mode) == 0o600
    assert manager.get_session() == SAMPLE_BLOB

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_manager_with_keyring(tmp_path):
    """
    A BrowserSessionManager whose keyring availability check returns True
    and whose file path is inside a temporary directory.

    WHY:  We need deterministic tests that never touch the real OS keyring
    or write files into the working directory.
    """
    with patch.object(BrowserSessionManager, "_check_keyring_available", return_value=True):
        manager = BrowserSessionManager(base_path=tmp_path)
    return manager


@pytest.fixture
def session_manager_no_keyring(tmp_path):
    """
    A BrowserSessionManager where the OS keyring is *not* available,
    so all storage must fall through to the JSON file.
    """
    with patch.object(BrowserSessionManager, "_check_keyring_available", return_value=False):
        manager = BrowserSessionManager(base_path=tmp_path)
    return manager


# =============================================================================
# 1.  KEYRING STORE / GET / DELETE ROUND-TRIP
# =============================================================================


class TestKeyringRoundTrip:
    """Verify full store -> get -> delete cycle through the (mocked) keyring."""

    def test_store_session_in_keyring_calls_set_password(self, session_manager_with_keyring):
        """store_session serialises the blob and passes it to keyring.set_password."""
        with patch("keyring.set_password") as mock_set:
            success, message = session_manager_with_keyring.store_session(SAMPLE_BLOB)

        assert success is True
        mock_set.assert_called_once_with(
            "coursewright",  # SERVICE_NAME
            "canvas_browser_session",  # KEY_NAME
            SAMPLE_BLOB_JSON,  # compact JSON
        )

    def test_get_session_from_keyring_deserialises_correctly(self, session_manager_with_keyring):
        """get_session returns the original dict when keyring holds the JSON blob."""
        with patch("keyring.get_password", return_value=SAMPLE_BLOB_JSON):
            retrieved = session_manager_with_keyring.get_session()

        assert retrieved == SAMPLE_BLOB
        assert retrieved["csrf_token"] == "csrf-token-value-here"
        assert len(retrieved["cookies"]) == 2

    def test_get_session_returns_none_when_keyring_empty(self, session_manager_with_keyring):
        """If keyring holds nothing and there is no file, get_session returns None."""
        with patch("keyring.get_password", return_value=None):
            retrieved = session_manager_with_keyring.get_session()

        assert retrieved is None

    def test_delete_session_calls_keyring_delete_password(self, session_manager_with_keyring):
        """delete_session removes the entry from the keyring."""
        with patch("keyring.delete_password") as mock_delete:
            success, message = session_manager_with_keyring.delete_session()

        assert success is True
        mock_delete.assert_called_once_with("coursewright", "canvas_browser_session")
        assert "keyring" in message.lower()


class TestKeyringWriteFailureFallsBackToFile:
    """A failed keyring write (e.g. Windows 2560-byte blob limit) must fall
    back to the JSON file rather than losing the session entirely."""

    WINDOWS_CREDENTIAL_ERROR = Exception("(1783, 'CredWrite', 'The stub received bad data.')")

    def test_oversized_blob_keyring_error_falls_back_to_file(self, session_manager_with_keyring):
        """When keyring.set_password raises, the blob is written to the file."""
        with patch("keyring.set_password", side_effect=self.WINDOWS_CREDENTIAL_ERROR):
            success, message = session_manager_with_keyring.store_session(SAMPLE_BLOB)

        assert success is True
        assert session_manager_with_keyring.file_path.exists()
        assert json.loads(session_manager_with_keyring.file_path.read_text()) == SAMPLE_BLOB

    def test_get_session_reads_file_after_keyring_write_fallback(
        self, session_manager_with_keyring
    ):
        """After a keyring-write fallback, get_session still returns the blob
        by reading the file when keyring holds nothing."""
        with patch("keyring.set_password", side_effect=self.WINDOWS_CREDENTIAL_ERROR):
            session_manager_with_keyring.store_session(SAMPLE_BLOB)

        with patch("keyring.get_password", return_value=None):
            retrieved = session_manager_with_keyring.get_session()

        assert retrieved == SAMPLE_BLOB


# =============================================================================
# 2.  FILE FALLBACK (no keyring)
# =============================================================================


class TestFileFallback:
    """Verify store / get / delete when keyring is unavailable."""

    # Helper constant so the assertion is readable
    FILE_NAME_FRAGMENT = ".canvas_session.json"

    def test_store_session_writes_json_file(self, session_manager_no_keyring):
        """
        When keyring is absent, the blob is written to .canvas_session.json.
        """
        success, message = session_manager_no_keyring.store_session(SAMPLE_BLOB)

        assert success is True
        assert session_manager_no_keyring.file_path.exists()

        stored_json = session_manager_no_keyring.file_path.read_text()
        assert json.loads(stored_json) == SAMPLE_BLOB

    def test_store_session_reports_file_fallback_warning(
        self, session_manager_no_keyring, caplog, capsys
    ):
        """
        File fallback must warn.  discussorator printed a Rich warning;
        library code is silent here, so the warning is returned in the
        message and logged for ``ui/`` to render.
        """
        with caplog.at_level(logging.WARNING):
            success, message = session_manager_no_keyring.store_session(SAMPLE_BLOB)

        assert success is True
        assert "keyring" in message.lower()
        assert self.FILE_NAME_FRAGMENT in message
        assert "keyring unavailable" in caplog.text.lower()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_get_session_reads_from_file(self, session_manager_no_keyring):
        """When the file exists, get_session deserialises and returns the blob."""
        session_manager_no_keyring.file_path.write_text(SAMPLE_BLOB_JSON)

        retrieved = session_manager_no_keyring.get_session()

        assert retrieved == SAMPLE_BLOB

    def test_get_session_returns_none_when_file_absent(self, session_manager_no_keyring):
        """No file + no keyring -> get_session returns None."""
        retrieved = session_manager_no_keyring.get_session()

        assert retrieved is None

    def test_delete_session_removes_file(self, session_manager_no_keyring):
        """delete_session deletes the JSON file when keyring is unavailable."""
        session_manager_no_keyring.file_path.write_text(SAMPLE_BLOB_JSON)

        success, message = session_manager_no_keyring.delete_session()

        assert success is True
        assert not session_manager_no_keyring.file_path.exists()
        assert self.FILE_NAME_FRAGMENT in message


# =============================================================================
# 3.  ``get_storage_method`` REPORTING
# =============================================================================


class TestGetStorageMethod:
    """Verify get_storage_method returns the correct StorageMethod enum value."""

    def test_reports_keyring_when_session_in_keyring(self, session_manager_with_keyring):
        with patch("keyring.get_password", return_value=SAMPLE_BLOB_JSON):
            method = session_manager_with_keyring.get_storage_method()

        assert method == StorageMethod.KEYRING

    def test_reports_file_when_json_file_exists(self, session_manager_with_keyring):
        """
        Even when keyring is available but empty, a present file is detected.
        """
        session_manager_with_keyring.file_path.write_text(SAMPLE_BLOB_JSON)

        with patch("keyring.get_password", return_value=None):
            method = session_manager_with_keyring.get_storage_method()

        assert method == StorageMethod.FILE

    def test_reports_none_when_nothing_stored(self, session_manager_with_keyring):
        with patch("keyring.get_password", return_value=None):
            method = session_manager_with_keyring.get_storage_method()

        assert method == StorageMethod.NONE

    def test_reports_file_when_no_keyring_and_file_present(self, session_manager_no_keyring):
        """No keyring + file present -> FILE."""
        session_manager_no_keyring.file_path.write_text(SAMPLE_BLOB_JSON)

        method = session_manager_no_keyring.get_storage_method()

        assert method == StorageMethod.FILE

    def test_reports_none_when_no_keyring_and_no_file(self, session_manager_no_keyring):
        """No keyring + no file -> NONE."""
        method = session_manager_no_keyring.get_storage_method()

        assert method == StorageMethod.NONE


# =============================================================================
# 4.  EMPTY BLOB HANDLING
# =============================================================================


class TestEmptyBlobHandling:
    """Verify that an empty or falsy blob is rejected immediately."""

    def test_empty_dict_rejected(self, session_manager_with_keyring):
        success, message = session_manager_with_keyring.store_session({})

        assert success is False
        assert "empty" in message.lower()

    def test_none_blob_rejected(self, session_manager_with_keyring):
        success, message = session_manager_with_keyring.store_session(None)

        assert success is False
        assert "empty" in message.lower()


# =============================================================================
# 5.  CORRUPT JSON GRACEFUL DEGRADATION
# =============================================================================


class TestCorruptJsonHandling:
    """Verify that corrupt stored values don't raise -- they return None."""

    def test_corrupt_json_in_keyring_returns_none(self, session_manager_with_keyring):
        """If keyring holds malformed JSON, get_session must return None."""
        with patch("keyring.get_password", return_value="NOT { valid JSON"):
            retrieved = session_manager_with_keyring.get_session()

        assert retrieved is None

    def test_corrupt_json_in_file_returns_none(self, session_manager_no_keyring):
        """If the JSON file is corrupted, get_session must return None."""
        session_manager_no_keyring.file_path.write_text("CORRUPTED DATA ][")

        retrieved = session_manager_no_keyring.get_session()

        assert retrieved is None


# =============================================================================
# 6.  ``is_keyring_available``
# =============================================================================


class TestIsKeyringAvailable:
    """Verify the convenience availability flag."""

    def test_reports_true_when_keyring_available(self, session_manager_with_keyring):
        assert session_manager_with_keyring.is_keyring_available() is True

    def test_reports_false_when_keyring_unavailable(self, session_manager_no_keyring):
        assert session_manager_no_keyring.is_keyring_available() is False


# =============================================================================
# 7.  DELETE cleans BOTH keyring AND file
# =============================================================================


class TestDeleteSessionCleansAllLocations:
    """delete_session should remove data from every tier it finds."""

    def test_deletes_keyring_and_file_together(self, session_manager_with_keyring):
        """
        When both a keyring entry and a file exist, both are removed.
        """
        session_manager_with_keyring.file_path.write_text(SAMPLE_BLOB_JSON)

        with patch("keyring.delete_password") as mock_delete:
            success, message = session_manager_with_keyring.delete_session()

        assert success is True
        mock_delete.assert_called_once()
        assert not session_manager_with_keyring.file_path.exists()

    def test_reports_nothing_found_when_already_clean(self, session_manager_with_keyring):
        """If there is nothing to delete, a friendly 'not found' message is returned."""
        # keyring.delete_password raises when the entry doesn't exist;
        # we simulate that here to exercise the "not found" branch.
        with patch(
            "keyring.delete_password",
            side_effect=Exception("not found in keyring"),
        ):
            success, message = session_manager_with_keyring.delete_session()

        # delete_session should still succeed overall (nothing was there)
        assert success is True


# =============================================================================
# 8.  FACTORY FUNCTION (get_browser_session_manager)
# =============================================================================


class TestGetBrowserSessionManagerFactory:
    """Verify the module-level singleton factory."""

    @pytest.fixture(autouse=True)
    def fresh_singleton(self, monkeypatch, tmp_path):
        """Start each test with no singleton, in a temporary directory."""
        monkeypatch.setattr(browser_session_module, "_default_browser_session_manager", None)
        monkeypatch.chdir(tmp_path)

    def test_factory_returns_browser_session_manager_instance(self):
        """get_browser_session_manager() returns a BrowserSessionManager."""
        from coursewright.core.auth import get_browser_session_manager

        manager = get_browser_session_manager()

        assert isinstance(manager, BrowserSessionManager)

    def test_factory_returns_same_instance_on_repeated_calls(self):
        """Singleton: repeated calls return the exact same object."""
        from coursewright.core.auth import get_browser_session_manager

        manager_a = get_browser_session_manager()
        manager_b = get_browser_session_manager()

        assert manager_a is manager_b


# =============================================================================
# 9.  SESSION SECRETS ARE NEVER SHOWN
# =============================================================================


class TestSessionBlobIsMasked:
    """Acceptance: cookie and CSRF values never appear in a repr or the log."""

    SECRET_VALUES = ("abc123", "xyz789", "csrf-token-value-here")

    def test_retrieved_blob_repr_and_str_are_masked(self, session_manager_no_keyring):
        session_manager_no_keyring.file_path.write_text(SAMPLE_BLOB_JSON)

        retrieved = session_manager_no_keyring.get_session()

        assert isinstance(retrieved, SessionBlob)
        for rendering in (repr(retrieved), str(retrieved), f"{retrieved}"):
            assert rendering == "SessionBlob(cookies=2, csrf_token=<set>)"
            for secret_value in self.SECRET_VALUES:
                assert secret_value not in rendering

    def test_blob_without_csrf_token_says_so(self):
        assert repr(SessionBlob({"cookies": []})) == "SessionBlob(cookies=0, csrf_token=<absent>)"

    def test_masked_blob_still_serialises_for_storage(self):
        """Masking is display only: the manager can store what it handed out."""
        assert json.dumps(SessionBlob(SAMPLE_BLOB), separators=(",", ":")) == SAMPLE_BLOB_JSON

    def test_messages_and_log_never_contain_session_values(
        self, session_manager_with_keyring, caplog
    ):
        """Store (keyring refuses, file fallback), read, report, delete."""
        messages: list[str] = []
        with (
            caplog.at_level(logging.DEBUG),
            patch("keyring.set_password", side_effect=Exception("backend refused")),
            patch("keyring.get_password", return_value=None),
            patch("keyring.delete_password"),
        ):
            messages.append(session_manager_with_keyring.store_session(SAMPLE_BLOB)[1])
            messages.append(repr(session_manager_with_keyring.get_session()))
            messages.append(str(session_manager_with_keyring.get_storage_method()))
            messages.append(session_manager_with_keyring.delete_session()[1])

        everything_shown = caplog.text + "\n".join(messages)
        for secret_value in self.SECRET_VALUES:
            assert secret_value not in everything_shown
