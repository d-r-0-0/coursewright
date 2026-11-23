"""Port of source auth command tests to silent library status/login functions.

Interactive prompting and CLI dispatch belong to the UI port; selected
identity dispatch is covered here at the library boundary instead.
"""

from unittest.mock import Mock

import pytest

from coursewright.design.media import panopto_auth as auth
from coursewright.design.media.panopto_auth import PanoptoClientCredentials


@pytest.fixture(autouse=True)
def prevent_network(monkeypatch):
    monkeypatch.setattr(
        "requests.sessions.Session.request", Mock(side_effect=AssertionError("network"))
    )


def test_login_stores_selected_identity_without_echoing(monkeypatch, capsys):
    store = Mock()
    monkeypatch.setattr(auth, "store_panopto_client_credentials", store)
    result = auth.login_panopto_client(" example-id ", " example-secret ", "user")
    assert result.exit_code == 0
    credentials, identity = store.call_args.args
    assert (credentials.client_id, credentials.client_secret, identity) == (
        "example-id",
        "example-secret",
        "user",
    )
    assert "example-id" not in repr(result)
    assert "example-secret" not in repr(result)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("values", [("", "example-secret"), ("example-id", " ")])
def test_login_refuses_partial_pair(monkeypatch, values):
    store = Mock()
    monkeypatch.setattr(auth, "store_panopto_client_credentials", store)
    assert auth.login_panopto_client(*values).exit_code == 1
    store.assert_not_called()


def test_login_reports_vault_failure_without_exception_values(monkeypatch, capsys):
    monkeypatch.setattr(
        auth, "store_panopto_client_credentials", Mock(side_effect=RuntimeError("example-secret"))
    )
    result = auth.login_panopto_client("example-id", "example-secret")
    assert result.exit_code == 1
    assert "example-secret" not in repr(result)
    assert capsys.readouterr().out == ""


def test_status_reports_source_without_values_or_network(monkeypatch, capsys):
    credentials = PanoptoClientCredentials("example-id", "example-secret", source="environment")
    loader = Mock(return_value=credentials)
    monkeypatch.setattr(auth, "load_panopto_client_credentials", loader)
    result = auth.check_panopto_client_status("user")
    assert result.exit_code == 0
    loader.assert_called_once_with("user")
    assert "environment" in result.message
    assert "validity has not been tested" in result.message
    assert "example-id" not in repr(result)
    assert "example-secret" not in repr(result)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("failure", [ValueError, RuntimeError, OSError])
def test_status_reports_resolution_errors_safely(monkeypatch, capsys, failure):
    monkeypatch.setattr(
        auth, "load_panopto_client_credentials", Mock(side_effect=failure("example-secret"))
    )
    result = auth.check_panopto_client_status()
    assert result.exit_code == 1
    assert "example-secret" not in repr(result)
    assert capsys.readouterr().out == ""


def test_status_missing_credentials_fails(monkeypatch):
    monkeypatch.setattr(auth, "load_panopto_client_credentials", Mock(return_value=None))
    assert auth.check_panopto_client_status().exit_code == 1


@pytest.mark.parametrize("client_type", ["server", "user"])
def test_library_dispatches_client_identity(monkeypatch, client_type):
    store = Mock()
    loader = Mock(return_value=None)
    monkeypatch.setattr(auth, "store_panopto_client_credentials", store)
    monkeypatch.setattr(auth, "load_panopto_client_credentials", loader)
    auth.login_panopto_client("example-id", "example-secret", client_type)
    assert store.call_args.args[1] == client_type
    auth.check_panopto_client_status(client_type)
    loader.assert_called_once_with(client_type)
