"""Source consumer regressions adapted to configured, silent library workflows.

The original tests imported private course scripts. No such files are copied:
browser callbacks/prompts are excluded by the port contract. Their credential
selection, request payload, redaction and endpoint checks are retained here.
"""

import os
import subprocess
import sys
from unittest.mock import Mock

import pytest
import requests

from coursewright.design.media import panopto_api as api
from coursewright.design.media import panopto_workflows as workflows
from coursewright.design.media.panopto_auth import PanoptoClientCredentials

PRIVATE_ID = "private-client-identifier"
PRIVATE_VALUE = "private-client-value"
BASE_URL = "https://panopto.example.edu/Panopto"


@pytest.fixture
def credential_loader(monkeypatch):
    loader = Mock(return_value=PanoptoClientCredentials(PRIVATE_ID, PRIVATE_VALUE))
    monkeypatch.setattr(workflows, "load_panopto_client_credentials", loader)
    return loader


@pytest.mark.parametrize(
    "name", ["panopto_api", "panopto_auth", "panopto_models", "panopto_workflows"]
)
def test_all_consumers_import_without_credentials_or_network(monkeypatch, panopto_transport, name):
    # First import must be safe, without replacing live Pydantic classes in
    # the rest of the test process through importlib.reload.
    script = f"""
import importlib
from unittest.mock import patch
with patch('keyring.get_password', side_effect=AssertionError('vault')):
    with patch('requests.Session.request', side_effect=AssertionError('network')):
        importlib.import_module('coursewright.design.media.{name}')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    panopto_transport.assert_not_called()


@pytest.mark.parametrize("client_type", ["server", "user"])
def test_loader_selects_correct_identity(monkeypatch, panopto_transport, client_type):
    loader = Mock(return_value=PanoptoClientCredentials(PRIVATE_ID, PRIVATE_VALUE))
    monkeypatch.setattr(api, "load_panopto_client_credentials", loader)
    client = api.create_panopto_client(BASE_URL, client_type=client_type)
    loader.assert_called_once_with(client_type)
    assert client.credentials.as_tuple() == (PRIVATE_ID, PRIVATE_VALUE)


def test_debug_url_redacts_credentials(credential_loader, capsys):
    result = workflows.build_oauth_debug_info(BASE_URL)
    credential_loader.assert_called_once_with("user")
    assert "REDACTED" in result.authorization_url
    assert PRIVATE_ID not in repr(result)
    assert PRIVATE_VALUE not in repr(result)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("response_failure", [False, True])
def test_oauth_passes_private_pair_without_printing_values(
    credential_loader,
    panopto_transport,
    http_response,
    capsys,
    caplog,
    traffic_events,
    response_failure,
):
    panopto_transport.side_effect = (
        requests.RequestException(PRIVATE_VALUE)
        if response_failure
        else [http_response(400, content=f"echo {PRIVATE_ID} {PRIVATE_VALUE}".encode())]
    )
    assert workflows.exchange_oauth_authorization_code(BASE_URL, "code", "verifier") is None
    credential_loader.assert_called_once_with("user")
    data = panopto_transport.call_args.kwargs["data"]
    assert data["client_id"] == PRIVATE_ID
    assert data["client_secret"] == PRIVATE_VALUE
    assert data["code"] == "code"
    assert data["code_verifier"] == "verifier"
    assert [event.status for event in traffic_events] == [0, -1 if response_failure else 400]
    output = capsys.readouterr().out + caplog.text + repr(traffic_events)
    assert PRIVATE_ID not in output
    assert PRIVATE_VALUE not in output


@pytest.mark.parametrize("use_environment", [False, True])
def test_password_grant_uses_shared_user_pair_and_private_login(
    credential_loader,
    panopto_transport,
    http_response,
    monkeypatch,
    capsys,
    caplog,
    use_environment,
):
    arguments = {}
    for name, value in [("username", "private-user"), ("password", "private-password")]:
        if use_environment:
            monkeypatch.setenv(f"PANOPTO_{name.upper()}", value)
        else:
            monkeypatch.delenv(f"PANOPTO_{name.upper()}", raising=False)
            arguments[name] = value
    panopto_transport.side_effect = [http_response(401, content=PRIVATE_VALUE.encode())]
    assert (
        workflows.probe_password_grant_folder_sessions(BASE_URL, "configured-folder", **arguments)
        is None
    )
    credential_loader.assert_called_once_with("user")
    assert panopto_transport.call_args.kwargs["auth"] == (PRIVATE_ID, PRIVATE_VALUE)
    data = panopto_transport.call_args.kwargs["data"]
    assert data["username"] == "private-user"
    assert data["password"] == "private-password"  # pragma: allowlist secret -- mock
    output = capsys.readouterr().out + caplog.text
    for value in (PRIVATE_ID, PRIVATE_VALUE, "private-user", "private-password"):
        assert value not in output


def test_video_auth_error_omits_response_body(panopto_transport, caplog):
    panopto_transport.side_effect = requests.HTTPError(PRIVATE_VALUE)
    client = api.PanoptoAPI("panopto.example.edu", PRIVATE_ID, PRIVATE_VALUE)
    assert client.authenticate() is False
    assert PRIVATE_ID not in caplog.text
    assert PRIVATE_VALUE not in caplog.text


def test_library_token_error_omits_untrusted_response_body(panopto_transport, http_response):
    panopto_transport.side_effect = [http_response(401, content=PRIVATE_VALUE.encode())] * 2
    client = api.PanoptoAPI(BASE_URL, PanoptoClientCredentials(PRIVATE_ID, PRIVATE_VALUE))
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        client._get_access_token()
    assert PRIVATE_VALUE not in str(error.value)


def test_password_grant_preserves_endpoint_and_folder_reads(
    credential_loader, panopto_transport, http_response, traffic_events, capsys
):
    panopto_transport.side_effect = [
        http_response(payload={"access_token": "private-access-token"}),
        *[http_response(payload={}) for _ in range(4)],
        http_response(payload={"Results": [{"Name": "Lecture", "Id": "one"}]}),
    ]
    results = workflows.probe_password_grant_folder_sessions(
        BASE_URL,
        "configured-folder",
        username="private-user",
        password="private-password",  # pragma: allowlist secret -- mocked login
    )
    assert results[0].name == "Lecture"
    assert panopto_transport.call_count == 6
    calls = panopto_transport.call_args_list
    assert [call.args[1] for call in calls[1:5]] == [
        BASE_URL + endpoint for endpoint in workflows.DIAGNOSTIC_ENDPOINTS
    ]
    assert calls[-1].args[1].endswith("/configured-folder/sessions")
    assert calls[-1].kwargs["headers"]["Authorization"] == "Bearer private-access-token"
    assert len(traffic_events) == 12
    assert "private-access-token" not in repr(traffic_events)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("operation", ["debug", "password", "oauth"])
def test_missing_user_client_stops_before_requests(credential_loader, panopto_transport, operation):
    credential_loader.return_value = None
    operations = {
        "debug": lambda: workflows.build_oauth_debug_info(BASE_URL),
        "password": lambda: workflows.probe_password_grant_folder_sessions(BASE_URL, "folder"),
        "oauth": lambda: workflows.exchange_oauth_authorization_code(BASE_URL, "code", "verifier"),
    }
    assert operations[operation]() is None
    panopto_transport.assert_not_called()


def test_oauth_success_saves_private_file_and_publishes(
    credential_loader, panopto_transport, http_response, tmp_path, traffic_events
):
    panopto_transport.side_effect = [http_response(payload={"access_token": PRIVATE_VALUE})]
    destination = tmp_path / "token.json"
    payload = workflows.exchange_oauth_authorization_code(
        BASE_URL, "code", "verifier", token_file=destination
    )
    assert payload["access_token"] == PRIVATE_VALUE
    assert destination.stat().st_mode & 0o777 == 0o600
    assert PRIVATE_VALUE not in repr(traffic_events)


def test_pkce_challenge_matches_verifier():
    import base64
    import hashlib

    verifier, challenge = workflows.generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert challenge == base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
