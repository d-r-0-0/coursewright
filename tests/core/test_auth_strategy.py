"""Tests for ``AuthStrategy`` and ``TokenAuth`` in ``core/canvas``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import SecretStr

from coursewright.core.canvas.auth_strategy import AuthStrategy, TokenAuth
from coursewright.core.canvas.client import CanvasClient, EnhancedCanvasAPI


class _ConstantHeaderAuth(AuthStrategy):
    """A trivial concrete strategy that injects a fixed custom header."""

    HEADER_NAME = "X-Test-Auth"
    HEADER_VALUE = "test-value-42"

    def get_headers(self) -> dict[str, str]:
        return {self.HEADER_NAME: self.HEADER_VALUE}

    def describe(self) -> str:
        return "constant test header"


class TestTokenAuth:
    """Unit tests for the ``TokenAuth`` strategy class."""

    def test_get_headers_returns_bearer_token_header(self) -> None:
        strategy = TokenAuth("my-secret-token")  # pragma: allowlist secret
        headers = strategy.get_headers()

        expected = "Bearer " + "my-secret-token"
        assert headers["Authorization"] == expected

    def test_accepts_secret_str_token(self) -> None:
        strategy = TokenAuth(SecretStr("secret-str-token"))
        headers = strategy.get_headers()

        assert headers["Authorization"] == "Bearer secret-str-token"

    def test_apply_to_session_injects_authorization_header(self) -> None:
        strategy = TokenAuth("session-token-abc")  # pragma: allowlist secret
        real_session = requests.Session()

        strategy.apply_to_session(real_session)

        expected = "Bearer " + "session-token-abc"
        assert real_session.headers["Authorization"] == expected

    def test_describe_returns_human_readable_label(self) -> None:
        strategy = TokenAuth("any-token")
        label = strategy.describe()

        assert isinstance(label, str)
        assert len(label) > 0
        assert "token" in label.lower() or "bearer" in label.lower()

    def test_empty_token_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            TokenAuth("")

    def test_empty_secret_str_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            TokenAuth(SecretStr(""))

    def test_repr_masks_token_secret(self) -> None:
        long_token = "super-secret-canvas-token-12345"  # pragma: allowlist secret
        strategy = TokenAuth(long_token)
        representation = repr(strategy)

        assert long_token not in representation
        assert long_token[:4] not in representation
        assert long_token not in str(strategy)
        assert long_token not in repr(vars(strategy))
        assert "TokenAuth" in representation

    def test_repr_masks_short_token_secret(self) -> None:
        short_token = "xy9"
        strategy = TokenAuth(short_token)

        assert short_token not in repr(strategy)
        assert short_token not in str(strategy)

    def test_get_headers_content_is_a_plain_dict(self) -> None:
        strategy = TokenAuth("plain-dict-token")
        result = strategy.get_headers()

        assert type(result) is dict  # noqa: E721


class TestCanvasClientAuthIntegration:
    """Verify that ``CanvasClient.__init__`` honours the auth strategy contract."""

    def test_enhanced_canvas_api_is_alias_for_canvas_client(self) -> None:
        assert EnhancedCanvasAPI is CanvasClient

    def test_classic_positional_call_sets_authorization_header(self) -> None:
        with patch("coursewright.core.canvas.client.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.headers = {}

            client = CanvasClient(
                "https://canvas.example.edu",
                "positional-token-xyz",
            )

        expected = "Bearer " + "positional-token-xyz"
        assert client.headers["Authorization"] == expected

    def test_classic_positional_call_strips_trailing_slash(self) -> None:
        with patch("coursewright.core.canvas.client.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.headers = {}

            client = CanvasClient(
                "https://canvas.example.edu///",
                "tok",
            )

        assert not client.base_url.endswith("/")

    def test_auth_strategy_kwarg_is_used_when_provided(self) -> None:
        custom_strategy = _ConstantHeaderAuth()

        with patch("coursewright.core.canvas.client.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.headers = {}

            client = CanvasClient(
                "https://canvas.example.edu",
                auth_strategy=custom_strategy,
            )

        assert client._auth_strategy is custom_strategy

    def test_custom_strategy_headers_appear_in_client_headers(self) -> None:
        custom_strategy = _ConstantHeaderAuth()

        with patch("coursewright.core.canvas.client.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.headers = {}

            client = CanvasClient(
                "https://canvas.example.edu",
                auth_strategy=custom_strategy,
            )

        assert client.headers.get(_ConstantHeaderAuth.HEADER_NAME) == (
            _ConstantHeaderAuth.HEADER_VALUE
        )

    def test_token_auth_strategy_is_stored_on_client(self) -> None:
        with patch("coursewright.core.canvas.client.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.headers = {}

            client = CanvasClient(
                "https://canvas.example.edu",
                "stored-token",
            )

        assert isinstance(client._auth_strategy, TokenAuth)

    def test_custom_strategy_describe_accessible_via_client(self) -> None:
        custom_strategy = _ConstantHeaderAuth()

        with patch("coursewright.core.canvas.client.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.headers = {}

            client = CanvasClient(
                "https://canvas.example.edu",
                auth_strategy=custom_strategy,
            )

        assert client._auth_strategy.describe() == "constant test header"
