"""Offline tests for atomic Panopto credential resolution and storage.

Ported from COURSE_BUILDER ``tests/test_panopto_credentials.py`` onto
``coursewright.design.media`` (resolving credentials through the
``coursewright.core.auth`` vault service namespace).
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from coursewright.core.auth import panopto as panopto_auth
from coursewright.design.media import panopto_api


@pytest.fixture
def credential_sources(monkeypatch, tmp_path):
    """Isolate the vault, environment and legacy file for every test."""
    for name in (
        "COURSEWRIGHT_PANOPTO_CLIENT_ID",
        "COURSEWRIGHT_PANOPTO_CLIENT_SECRET",
        "COURSEWRIGHT_PANOPTO_SCOPE",
        "COURSEWRIGHT_PANOPTO_USER_CLIENT_ID",
        "COURSEWRIGHT_PANOPTO_USER_CLIENT_SECRET",
        "COURSEWRIGHT_PANOPTO_USER_SCOPE",
        "PANOPTO_CLIENT_ID",
        "PANOPTO_CLIENT_SECRET",
        "PANOPTO_SCOPE",
        "PANOPTO_USER_CLIENT_ID",
        "PANOPTO_USER_CLIENT_SECRET",
        "PANOPTO_USER_SCOPE",
    ):
        monkeypatch.delenv(name, raising=False)
    vault_read = Mock(return_value=None)
    vault_write = Mock()
    monkeypatch.setattr(panopto_auth.keyring, "get_password", vault_read)
    monkeypatch.setattr(panopto_auth.keyring, "set_password", vault_write)
    legacy_file = tmp_path / "panopto.key"
    monkeypatch.setattr(panopto_auth, "PANOPTO_LEGACY_KEY_PATH", legacy_file)
    monkeypatch.setattr(panopto_api, "PANOPTO_LEGACY_KEY_PATH", legacy_file)
    return vault_read, vault_write, legacy_file


def test_vault_pair_wins_without_reading_lower_tiers(credential_sources, monkeypatch):
    vault_read, _, legacy_file = credential_sources
    vault_read.return_value = json.dumps(
        {
            "client_id": "vault-id",
            "client_secret": "vault-value",  # pragma: allowlist secret
            "scope": "api",
        }
    )
    monkeypatch.setenv("PANOPTO_CLIENT_ID", "partial-environment")
    legacy_file.write_text("malformed", encoding="utf-8")
    credentials = panopto_api.load_panopto_client_credentials()
    assert credentials is not None
    assert credentials.client_id == "vault-id"
    assert credentials.client_secret == "vault-value"  # pragma: allowlist secret
    assert credentials.source == "vault"
    vault_read.assert_called_once_with("coursewright", "panopto_server_client")


@pytest.mark.parametrize(
    ("client_type", "prefix"),
    [
        ("server", "PANOPTO"),
        ("user", "PANOPTO_USER"),
        ("server", "COURSEWRIGHT_PANOPTO"),
        ("user", "COURSEWRIGHT_PANOPTO_USER"),
    ],
)
def test_environment_pair_wins(credential_sources, monkeypatch, client_type, prefix):
    _, _, legacy_file = credential_sources
    legacy_file.write_text("legacy-id\nlegacy-value", encoding="utf-8")
    monkeypatch.setenv(f"{prefix}_CLIENT_ID", " environment-id ")
    monkeypatch.setenv(f"{prefix}_CLIENT_SECRET", " environment-value ")
    credentials = panopto_api.load_panopto_client_credentials(client_type)
    assert credentials is not None
    assert credentials.client_id == "environment-id"
    assert credentials.client_secret == "environment-value"  # pragma: allowlist secret
    assert credentials.source == "environment"


@pytest.mark.parametrize("present_name", ["PANOPTO_CLIENT_ID", "PANOPTO_CLIENT_SECRET"])
def test_partial_environment_never_borrows_from_file(
    credential_sources,
    monkeypatch,
    present_name,
):
    _, _, legacy_file = credential_sources
    legacy_file.write_text("legacy-id\nlegacy-value", encoding="utf-8")
    monkeypatch.setenv(present_name, "private-value")
    with pytest.raises(ValueError, match="environment") as error:
        panopto_api.load_panopto_client_credentials()
    assert "private-value" not in str(error.value)


@pytest.mark.parametrize(
    "vault_payload",
    [
        "",
        "private-invalid-json",
        "[]",
        "{}",
        '{"client_id": "private-value"}',  # pragma: allowlist secret
        '{"client_id": "id", "client_secret": 12}',
        '{"client_id": "id", "client_secret": "value", "scope": null}',
    ],
)
def test_malformed_vault_fails_closed(credential_sources, monkeypatch, vault_payload):
    vault_read, _, _ = credential_sources
    vault_read.return_value = vault_payload
    monkeypatch.setenv("PANOPTO_CLIENT_ID", "valid-env-id")
    monkeypatch.setenv("PANOPTO_CLIENT_SECRET", "valid-env-value")
    with pytest.raises(ValueError, match="vault") as error:
        panopto_api.load_panopto_client_credentials()
    assert "private" not in str(error.value)


def test_unavailable_vault_warns_without_backend_values(credential_sources, monkeypatch):
    vault_read, _, _ = credential_sources
    vault_read.side_effect = RuntimeError("private-backend-message")
    monkeypatch.setenv("PANOPTO_CLIENT_ID", "id")
    monkeypatch.setenv("PANOPTO_CLIENT_SECRET", "value")
    with pytest.warns(RuntimeWarning, match="vault unavailable") as caught:
        credentials = panopto_api.load_panopto_client_credentials()
    assert credentials is not None
    assert credentials.source == "environment"
    assert "private-backend" not in str(caught[0].message)


def test_legacy_bom_comments_and_warning(credential_sources):
    _, _, legacy_file = credential_sources
    legacy_file.write_text("\ufeff# comment\n id \n value \n", encoding="utf-8")
    with pytest.warns(RuntimeWarning, match="legacy"):
        credentials = panopto_api.load_panopto_client_credentials()
    assert credentials is not None
    assert credentials.client_id == "id"
    assert credentials.client_secret == "value"  # pragma: allowlist secret
    assert credentials.source == "legacy-file"


@pytest.mark.parametrize("text", ["", "id", "id\nvalue\nextra"])
def test_malformed_legacy_file_fails(credential_sources, text):
    _, _, legacy_file = credential_sources
    legacy_file.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="complete pair"):
        panopto_api.load_panopto_client_credentials()


def test_legacy_read_failure_keeps_chain(credential_sources, monkeypatch):
    def fail_read(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(panopto_auth.Path, "read_text", fail_read)
    with pytest.raises(RuntimeError, match="Unable to read") as error:
        panopto_api.load_panopto_client_credentials()
    assert isinstance(error.value.__cause__, PermissionError)


def test_user_never_borrows_server_sources(credential_sources, monkeypatch):
    _, _, legacy_file = credential_sources
    monkeypatch.setenv("PANOPTO_CLIENT_ID", "server-id")
    monkeypatch.setenv("PANOPTO_CLIENT_SECRET", "server-value")
    legacy_file.write_text("server-id\nserver-value", encoding="utf-8")
    assert panopto_api.load_panopto_client_credentials("user") is None


def test_missing_credentials_return_none(credential_sources):
    assert panopto_api.load_panopto_client_credentials() is None


@pytest.mark.parametrize("client_type", ["server", "user"])
def test_store_uses_atomic_identity_entry(credential_sources, client_type):
    _, vault_write, _ = credential_sources
    credentials = panopto_api.PanoptoClientCredentials("id", "private-value")
    panopto_api.store_panopto_client_credentials(credentials, client_type)
    service, key, payload = vault_write.call_args.args
    assert service == "coursewright"
    assert key == f"panopto_{client_type}_client"
    assert json.loads(payload) == {
        "client_id": "id",
        "client_secret": "private-value",  # pragma: allowlist secret
        "scope": "api",
    }
    assert "private-value" not in repr(credentials)
    assert "client_id=" not in repr(credentials)


def test_store_failure_is_sanitized(credential_sources):
    _, vault_write, _ = credential_sources
    vault_write.side_effect = RuntimeError("private-value")
    credentials = panopto_api.PanoptoClientCredentials("id", "private-value")
    with pytest.raises(RuntimeError, match="Unable to store") as error:
        panopto_api.store_panopto_client_credentials(credentials)
    assert "private-value" not in str(error.value)
    assert error.value.__suppress_context__


def test_invalid_pair_never_reaches_vault(credential_sources):
    _, vault_write, _ = credential_sources
    with pytest.raises(ValueError):
        panopto_api.store_panopto_client_credentials(panopto_api.PanoptoClientCredentials("id", ""))
    vault_write.assert_not_called()


def test_invalid_identity_rejected_before_vault_read(credential_sources):
    vault_read, _, _ = credential_sources
    with pytest.raises(ValueError, match="client type"):
        panopto_api.load_panopto_client_credentials("other")
    vault_read.assert_not_called()


@pytest.mark.parametrize("part", ["CLIENT_ID", "CLIENT_SECRET"])
def test_environment_namespaces_never_mix_pairs(credential_sources, monkeypatch, part):
    monkeypatch.setenv("PANOPTO_CLIENT_ID", "legacy-id")
    monkeypatch.setenv("PANOPTO_CLIENT_SECRET", "legacy-value")
    monkeypatch.setenv(f"COURSEWRIGHT_PANOPTO_{part}", "new-partial-value")
    with pytest.raises(ValueError, match="environment"):
        panopto_api.load_panopto_client_credentials()
