"""Tests for the LLM client.

Ported from discussorator ``tests/test_llm_integration.py``.  That
file also tested ``FeedbackGenerator``; those regressions live in
``tests/ta/grading/test_feedback_{legacy,roles,prompts}.py``. Its
end-to-end test is preserved there; here it also drives
``LLMClient.generate_sync`` directly.

Nothing here reaches a provider: ``httpx.Client`` is replaced by a
mock.  The one test that would need a live service is marked
``integration`` and is skipped by the gate.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import Mock, patch

import httpx
import pytest

from coursewright.core.llm import cost_tracker
from coursewright.core.llm.client import (
    LLMClient,
    LLMConfig,
    LLMProvider,
    LLMResponse,
    create_client,
)

SYNTHETIC_API_KEY = "test_key_12345"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def usage_records(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture usage records instead of writing the real usage file."""
    recorded: list[dict] = []
    monkeypatch.setattr(cost_tracker, "record_usage", lambda **fields: recorded.append(fields))
    return recorded


def openai_reply(content: str = "Excellent analysis of the topic!") -> Mock:
    """A mocked 200 answer in OpenAI chat-completions shape."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "model": "gpt-4o-mini",
        "usage": {"total_tokens": 45},
    }
    return mock_response


def anthropic_reply(content: str = "A thoughtful reply.") -> Mock:
    """A mocked 200 answer in Anthropic messages shape."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "content": [{"text": content}],
        "model": "claude-sonnet-4-6",
        "usage": {"input_tokens": 30, "output_tokens": 12},
        "stop_reason": "end_turn",
    }
    return mock_response


def http_status_error(status_code: int, body: dict | None = None) -> httpx.HTTPStatusError:
    """The error ``raise_for_status`` raises for a 4xx/5xx answer."""
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, json=body or {}, request=request)
    return httpx.HTTPStatusError("failed", request=request, response=response)


class TestLLMClient:
    """Test LLM client functionality"""

    def test_llm_config_creation(self):
        """Test creating LLM configuration"""
        config = LLMConfig(
            provider=LLMProvider.OPENAI, model="gpt-4o-mini", temperature=0.7, max_tokens=300
        )

        assert config.provider == LLMProvider.OPENAI
        assert config.model == "gpt-4o-mini"
        assert config.temperature == 0.7
        assert config.max_tokens == 300

    def test_llm_config_validation(self):
        """Test model validation"""
        config = LLMConfig(provider=LLMProvider.OPENAI, model="gpt-4o")

        assert config.validate_model() is True

    def test_llm_config_repr_hides_api_key(self):
        """A printed or logged config never shows the secret."""
        config = LLMConfig(provider=LLMProvider.OPENAI, api_key=SYNTHETIC_API_KEY)

        assert SYNTHETIC_API_KEY not in repr(config)
        assert SYNTHETIC_API_KEY not in str(config)

    @patch.dict("os.environ", {"OPENAI_API_KEY": SYNTHETIC_API_KEY})
    def test_get_api_key_from_env(self):
        """Test getting API key from environment"""
        config = LLMConfig(provider=LLMProvider.OPENAI)
        api_key = config.get_api_key()

        assert api_key == SYNTHETIC_API_KEY

    @patch.dict("os.environ", {}, clear=True)
    def test_llm_client_requires_api_key(self):
        """Test that LLM client requires API key"""
        config = LLMConfig(provider=LLMProvider.OPENAI, model="gpt-4o-mini")

        with pytest.raises(ValueError, match="No API key found"):
            LLMClient(config)

    @patch.dict("os.environ", {"OPENAI_API_KEY": SYNTHETIC_API_KEY})
    def test_gpt5_payload_uses_low_reasoning_and_no_temperature(self):
        """GPT-5 chat payloads should not send custom temperature."""
        client = LLMClient(LLMConfig(provider=LLMProvider.OPENAI, model="gpt-5.5"))

        payload = client._build_openai_payload(
            messages=[{"role": "user", "content": "Return TSV only."}],
            temperature=0.0,
            max_tokens=4000,
        )

        assert payload["max_completion_tokens"] == 4000
        assert payload["reasoning_effort"] == "low"
        assert "temperature" not in payload

    @patch.dict("os.environ", {"OPENAI_API_KEY": SYNTHETIC_API_KEY})
    def test_gpt6_payload_uses_completion_tokens_and_low_reasoning(self):
        """GPT-6 chat payloads should use the reasoning-model parameters."""
        client = LLMClient(LLMConfig(provider=LLMProvider.OPENAI, model="gpt-6.1-sol"))

        payload = client._build_openai_payload(
            messages=[{"role": "user", "content": "Return TSV only."}],
            temperature=0.0,
            max_tokens=4000,
        )

        assert payload["max_completion_tokens"] == 4000
        assert payload["reasoning_effort"] == "low"
        assert "temperature" not in payload

    @patch.dict("os.environ", {"OPENAI_API_KEY": SYNTHETIC_API_KEY})
    def test_non_reasoning_payload_keeps_temperature(self):
        """Non-reasoning chat payloads should retain temperature."""
        client = LLMClient(LLMConfig(provider=LLMProvider.OPENAI, model="gpt-4o"))

        payload = client._build_openai_payload(
            messages=[{"role": "user", "content": "Say hi."}],
            temperature=0.2,
            max_tokens=300,
        )

        assert payload["max_tokens"] == 300
        assert payload["temperature"] == 0.2
        assert "reasoning_effort" not in payload

    def test_create_client_fills_in_a_default_model_per_provider(self):
        """The factory picks a model when the caller names only a provider."""
        openai_client = create_client(LLMProvider.OPENAI, api_key=SYNTHETIC_API_KEY)
        anthropic_client = create_client(LLMProvider.ANTHROPIC, api_key=SYNTHETIC_API_KEY)

        assert openai_client.config.model == "gpt-4o"
        assert anthropic_client.config.model == "claude-3-5-sonnet-20241022"


class TestGenerateSync:
    """The mocked end-to-end flow, one provider at a time."""

    @patch("httpx.Client")
    def test_full_generation_flow(self, mock_httpx, usage_records):
        """Test complete generation flow against a mocked OpenAI answer"""
        mock_client_instance = Mock()
        mock_client_instance.post.return_value = openai_reply()
        mock_httpx.return_value = mock_client_instance

        config = LLMConfig(
            provider=LLMProvider.OPENAI, model="gpt-4o-mini", api_key=SYNTHETIC_API_KEY
        )
        client = LLMClient(config)

        response = client.generate_sync(
            "Write feedback for a synthetic student.", system_prompt="Be brief."
        )

        assert isinstance(response, LLMResponse)
        assert response.content == "Excellent analysis of the topic!"
        assert response.provider == LLMProvider.OPENAI
        assert response.tokens_used == 45
        assert response.finish_reason == "stop"

        url = mock_client_instance.post.call_args.args[0]
        sent = mock_client_instance.post.call_args.kwargs
        assert url == "https://api.openai.com/v1/chat/completions"
        assert sent["headers"]["Authorization"] == f"Bearer {SYNTHETIC_API_KEY}"
        assert sent["json"]["messages"] == [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Write feedback for a synthetic student."},
        ]
        # Usage is handed to the cost tracker after a successful call.
        assert usage_records == [{"model": "gpt-4o-mini", "provider": "openai", "tokens_used": 45}]

    @patch("httpx.Client")
    def test_anthropic_generation_flow(self, mock_httpx, usage_records):
        mock_client_instance = Mock()
        mock_client_instance.post.return_value = anthropic_reply()
        mock_httpx.return_value = mock_client_instance

        client = LLMClient(
            LLMConfig(
                provider=LLMProvider.ANTHROPIC,
                model="claude-sonnet-4-6",
                api_key=SYNTHETIC_API_KEY,
            )
        )
        response = client.generate_sync("Say hi.", system_prompt="Be brief.", max_tokens=50)

        assert response.content == "A thoughtful reply."
        assert response.provider == LLMProvider.ANTHROPIC
        assert response.tokens_used == 42
        assert response.finish_reason == "end_turn"

        url = mock_client_instance.post.call_args.args[0]
        sent = mock_client_instance.post.call_args.kwargs
        assert url == "https://api.anthropic.com/v1/messages"
        assert sent["headers"]["x-api-key"] == SYNTHETIC_API_KEY
        assert sent["json"]["system"] == "Be brief."
        assert sent["json"]["max_tokens"] == 50
        assert usage_records[0]["provider"] == "anthropic"

    @patch("httpx.Client")
    async def test_async_generate_returns_the_same_response(self, mock_httpx):
        mock_client_instance = Mock()
        mock_client_instance.post.return_value = openai_reply("Async reply.")
        mock_httpx.return_value = mock_client_instance

        client = LLMClient(LLMConfig(model="gpt-4o-mini", api_key=SYNTHETIC_API_KEY))
        response = await client.generate("Say hi.")

        assert response.content == "Async reply."

    @patch("httpx.Client")
    def test_a_failing_cost_tracker_never_fails_the_call(self, mock_httpx, monkeypatch):
        mock_client_instance = Mock()
        mock_client_instance.post.return_value = openai_reply()
        mock_httpx.return_value = mock_client_instance

        def broken_record_usage(**fields):
            raise OSError("disk full")

        monkeypatch.setattr(cost_tracker, "record_usage", broken_record_usage)
        client = LLMClient(LLMConfig(model="gpt-4o-mini", api_key=SYNTHETIC_API_KEY))

        assert client.generate_sync("Say hi.").content == "Excellent analysis of the topic!"

    @pytest.mark.parametrize(
        ("status_code", "expected_message"),
        [
            (401, "authentication failed"),
            (429, "rate limit or quota exceeded. Slow down"),
            (500, "server error"),
            (503, r"OpenAI API error \(503\)"),
        ],
    )
    @patch("httpx.Client")
    def test_openai_http_errors_become_actionable_value_errors(
        self, mock_httpx, status_code, expected_message
    ):
        failed_response = Mock()
        failed_response.raise_for_status.side_effect = http_status_error(
            status_code, {"error": {"message": "Slow down"}}
        )
        mock_client_instance = Mock()
        mock_client_instance.post.return_value = failed_response
        mock_httpx.return_value = mock_client_instance

        client = LLMClient(LLMConfig(model="gpt-4o-mini", api_key=SYNTHETIC_API_KEY))

        with pytest.raises(ValueError, match=expected_message) as raised:
            client.generate_sync("Say hi.")
        # AGENTS.md 3.4: the original error stays on the chain.
        assert isinstance(raised.value.__cause__, httpx.HTTPStatusError)

    @patch("httpx.Client")
    def test_verbose_logs_instead_of_printing(self, mock_httpx, caplog, capsys):
        """Library code is silent: verbose detail goes to logging."""
        mock_client_instance = Mock()
        mock_client_instance.post.return_value = openai_reply()
        mock_httpx.return_value = mock_client_instance

        client = LLMClient(LLMConfig(model="gpt-4o-mini", api_key=SYNTHETIC_API_KEY), verbose=True)
        with caplog.at_level(logging.INFO, logger="coursewright.core.llm.client"):
            client.generate_sync("Say hi.", system_prompt="Be brief.")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
        assert "Say hi." not in caplog.text
        assert "Be brief." not in caplog.text
        assert "LLM request: model=" in caplog.text
        assert "Excellent analysis of the topic!" not in caplog.text
        assert "LLM response: tokens=" in caplog.text
        assert SYNTHETIC_API_KEY not in caplog.text


@pytest.mark.integration
def test_live_openai_round_trip():
    """Talks to the real OpenAI API.  Needs OPENAI_API_KEY; not run by the gate."""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set")
    client = create_client(LLMProvider.OPENAI, model="gpt-5-nano", max_tokens=200)

    response = client.generate_sync("Reply with the single word: ready")

    assert response.content
    assert response.provider == LLMProvider.OPENAI
