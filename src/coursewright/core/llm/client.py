"""The one client for LLM providers (OpenAI and Anthropic).

Ported from discussorator ``llm/client.py`` and reshaped to the API
model in docs/HOUSE_STYLE.md section 1:

- **One client per remote service.**  Nothing else in Coursewright
  opens a connection to an LLM provider.
- **One choke point.**  Every provider call goes through
  `LLMClient._make_request`, which owns the HTTP round trip and the
  traffic events (docs/HOUSE_STYLE.md section 3), so no call can
  skip them.
- **Library code is silent** (AGENTS.md 3.8).  discussorator printed
  Rich panels when ``verbose`` was set; here the same detail goes to
  the standard ``logging`` module and ``ui/`` decides how to show it.

The provider-specific parts are small and sit on either side of the
choke point: one function builds the request (URL, headers, payload)
and one reads the reply into an `LLMResponse`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel, Field

from coursewright.core.traffic import (
    STATUS_ERRORED,
    STATUS_IN_FLIGHT,
    publish_payload_echo,
    publish_round_trip,
)

logger = logging.getLogger(__name__)

# The `source` every traffic event from this client carries.
TRAFFIC_SOURCE = "llm"

# Provider endpoints, and the short label each one shows in traffic
# output (the `target` of its traffic events).
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_TRAFFIC_TARGET = "chat.completions"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_TRAFFIC_TARGET = "messages"
ANTHROPIC_API_VERSION = "2023-06-01"

# How long one provider call may take.  discussorator used 60 seconds
# and that is kept: a batch of feedback from a reasoning model
# routinely needs longer than the 30 seconds that suit Canvas.
REQUEST_TIMEOUT_SECONDS = 60.0


class LLMProvider(str, Enum):  # noqa: UP042 - str() of a member must stay "LLMProvider.X"
    """Supported LLM providers"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class LLMConfig(BaseModel):
    """LLM API configuration"""

    provider: LLMProvider = LLMProvider.OPENAI
    # Kept out of repr() so a logged or printed config never shows the
    # secret (docs/HOUSE_STYLE.md section 1).
    api_key: str | None = Field(default=None, repr=False)
    model: str = "gpt-5-nano"  # Default configured model
    temperature: float = 0.7
    max_tokens: int = 1000

    # Model mappings
    openai_models: list[str] = Field(
        default_factory=lambda: [
            # GPT-6 Series (Latest)
            "gpt-6-astra",
            "gpt-6.1-sol",
            "gpt-6-sol",
            "gpt-6-luna",
            # GPT-5.6 Series
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            # GPT-5.5 Series
            "gpt-5.5",
            "gpt-5.5-pro",
            # GPT-5.4 Series
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            "gpt-5.4-pro",
            # Earlier GPT-5 models
            "gpt-5.2",
            "gpt-5.2-pro",
            "gpt-5.1",
            "gpt-5",
            "gpt-5-mini",
            "gpt-5-nano",
            "gpt-5-pro",
            # Reasoning models
            "o3",
            "o4-mini",
            # GPT-4 Series (Legacy)
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
        ]
    )
    anthropic_models: list[str] = Field(
        default_factory=lambda: [
            # Claude 4.6 Series (Latest)
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            # Claude 4 Series (Legacy)
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            # Claude 3.7 Series
            "claude-3-7-sonnet-20250219",
            # Legacy
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
        ]
    )

    def get_api_key(self) -> str:
        """Get API key from config or environment"""
        if self.api_key:
            return self.api_key

        # Try environment variables
        if self.provider == LLMProvider.OPENAI:
            return os.getenv("OPENAI_API_KEY", "")
        elif self.provider == LLMProvider.ANTHROPIC:
            return os.getenv("ANTHROPIC_API_KEY", "")

        return ""

    def validate_model(self) -> bool:
        """Validate that model is appropriate for provider"""
        if self.provider == LLMProvider.OPENAI:
            return any(self.model.startswith(m.split("-")[0]) for m in self.openai_models)
        elif self.provider == LLMProvider.ANTHROPIC:
            return any(self.model.startswith("claude") for _ in self.anthropic_models)
        return False


class LLMResponse(BaseModel):
    """LLM API response"""

    content: str
    model: str
    provider: LLMProvider
    tokens_used: int | None = None
    finish_reason: str | None = None


class LLMClient:
    """Unified LLM API client for OpenAI and Anthropic"""

    def __init__(self, config: LLMConfig, verbose: bool = False):
        """Initialize LLM client with configuration

        Args:
            config: LLM configuration
            verbose: If True, log the full request and response of
                every call (prompts included) to this module's logger
        """
        self.config = config
        self.api_key = config.get_api_key()
        self.verbose = verbose

        if not self.api_key:
            raise ValueError(
                f"No API key found for {config.provider}. "
                f"Set {config.provider.upper()}_API_KEY environment variable "
                f"or provide api_key in config."
            )

        self.client = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    def __del__(self):
        """Cleanup HTTP client"""
        if hasattr(self, "client"):
            self.client.close()

    # ------------------------------------------------------------------
    # The choke point
    # ------------------------------------------------------------------

    def _make_request(
        self,
        traffic_target: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        prompt: str,
    ) -> httpx.Response:
        """Send one request to a provider.  Every call goes through here.

        Publishes, in order: an echo of the prompt going out, an
        in-flight event, then exactly one closing event carrying the
        HTTP status (or `STATUS_ERRORED` when no response came back:
        timeout, connection reset).  Publishing never raises, so a
        broken subscriber cannot disturb the call.

        Errors are re-raised untouched (``httpx.HTTPStatusError`` for a
        4xx/5xx answer); callers decide how to word them.
        """
        request_started_at = time.monotonic()
        publish_payload_echo(TRAFFIC_SOURCE, self.config.model, traffic_target, "out", prompt)
        publish_round_trip(
            TRAFFIC_SOURCE,
            self.config.model,
            traffic_target,
            STATUS_IN_FLIGHT,
            request_started_at,
        )
        try:
            response = self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as http_status_error:
            failed_status_code = http_status_error.response.status_code
            publish_round_trip(
                TRAFFIC_SOURCE,
                self.config.model,
                traffic_target,
                failed_status_code,
                request_started_at,
            )
            logger.error(
                "LLM request to %s (%s) failed with HTTP %s",
                traffic_target,
                self.config.model,
                failed_status_code,
            )
            raise
        except Exception as request_error:
            publish_round_trip(
                TRAFFIC_SOURCE,
                self.config.model,
                traffic_target,
                STATUS_ERRORED,
                request_started_at,
            )
            logger.error(
                "LLM request to %s (%s) failed: %s",
                traffic_target,
                self.config.model,
                type(request_error).__name__,
            )
            raise
        publish_round_trip(
            TRAFFIC_SOURCE,
            self.config.model,
            traffic_target,
            response.status_code,
            request_started_at,
        )
        return response

    # ------------------------------------------------------------------
    # Public generation methods
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """
        Generate text using the configured LLM, from async code.

        Runs `generate_sync` on a worker thread so the event loop (a
        Textual application, say) stays responsive while the provider
        answers.  Same arguments, result and errors as `generate_sync`.
        """
        return await asyncio.to_thread(
            self.generate_sync, prompt, system_prompt, temperature, max_tokens
        )

    def generate_sync(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """
        Generate text using the configured LLM.

        Args:
            prompt: The user prompt/message
            system_prompt: Optional system prompt for context
            temperature: Override default temperature
            max_tokens: Override default max_tokens

        Returns:
            LLMResponse with generated text and metadata
        """
        temp = temperature if temperature is not None else self.config.temperature
        tokens = max_tokens if max_tokens is not None else self.config.max_tokens

        if self.config.provider == LLMProvider.OPENAI:
            response = self._generate_openai_sync(prompt, system_prompt, temp, tokens)
        elif self.config.provider == LLMProvider.ANTHROPIC:
            response = self._generate_anthropic_sync(prompt, system_prompt, temp, tokens)
        else:
            raise ValueError(f"Unsupported provider: {self.config.provider}")

        # Persist usage for cost tracking (best-effort, never fail the call)
        if response.tokens_used:
            try:
                # Imported here, not at the top: cost_tracker imports
                # model_registry, which imports this module.
                from coursewright.core.llm.cost_tracker import record_usage

                record_usage(
                    model=response.model,
                    provider=response.provider.value,
                    tokens_used=response.tokens_used,
                )
            except Exception:
                logger.debug("Could not record LLM usage", exc_info=True)

        return response

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    def _build_openai_payload(
        self,
        messages: list,
        temperature: float,
        max_tokens: int,
    ) -> dict:
        """Build an OpenAI chat completions payload with model-specific quirks.

        - GPT-5*, GPT-6*, and GPT-4.1* use ``max_completion_tokens`` instead of
          ``max_tokens``.
        - Reasoning models only accept the default temperature, so we omit it.
        - Reasoning models can spend completion tokens on hidden reasoning.
          For Coursewright's short structured tasks, request low reasoning
          effort so visible output is not starved by the token budget.
        """
        model_lower = self.config.model.lower()
        uses_completion_tokens = self.config.model.startswith(("gpt-5", "gpt-6", "gpt-4.1"))
        is_reasoning_style_model = model_lower.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))

        payload: dict = {
            "model": self.config.model,
            "messages": messages,
        }
        if not is_reasoning_style_model:
            payload["temperature"] = temperature
        if is_reasoning_style_model:
            payload["reasoning_effort"] = "low"
        if uses_completion_tokens:
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
        return payload

    def _generate_openai_sync(
        self, prompt: str, system_prompt: str | None, temperature: float, max_tokens: int
    ) -> LLMResponse:
        """Synchronous OpenAI API call"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        payload = self._build_openai_payload(messages, temperature, max_tokens)
        self._log_verbose_request(prompt, system_prompt, temperature, max_tokens)

        try:
            response = self._make_request(
                OPENAI_TRAFFIC_TARGET, OPENAI_CHAT_COMPLETIONS_URL, headers, payload, prompt
            )
        except httpx.HTTPStatusError as http_status_error:
            raise _describe_openai_http_error(http_status_error) from http_status_error

        data = response.json()

        llm_response = LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=data["model"],
            provider=LLMProvider.OPENAI,
            tokens_used=data.get("usage", {}).get("total_tokens"),
            finish_reason=data["choices"][0].get("finish_reason"),
        )
        self._publish_response_echo(OPENAI_TRAFFIC_TARGET, llm_response.content)
        self._log_verbose_response(llm_response)
        return llm_response

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------

    def _generate_anthropic_sync(
        self, prompt: str, system_prompt: str | None, temperature: float, max_tokens: int
    ) -> LLMResponse:
        """Synchronous Anthropic API call"""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }

        if system_prompt:
            payload["system"] = system_prompt

        self._log_verbose_request(prompt, system_prompt, temperature, max_tokens)
        response = self._make_request(
            ANTHROPIC_TRAFFIC_TARGET, ANTHROPIC_MESSAGES_URL, headers, payload, prompt
        )

        data = response.json()

        anthropic_response = LLMResponse(
            content=data["content"][0]["text"],
            model=data["model"],
            provider=LLMProvider.ANTHROPIC,
            tokens_used=data.get("usage", {}).get("input_tokens", 0)
            + data.get("usage", {}).get("output_tokens", 0),
            finish_reason=data.get("stop_reason"),
        )
        self._publish_response_echo(ANTHROPIC_TRAFFIC_TARGET, anthropic_response.content)
        self._log_verbose_response(anthropic_response)
        return anthropic_response

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _publish_response_echo(self, traffic_target: str, content: str) -> None:
        """Echo the head of the model's completion text as inbound
        traffic chunks.  Best-effort; silent on failure."""
        publish_payload_echo(TRAFFIC_SOURCE, self.config.model, traffic_target, "in", content)

    def _log_verbose_request(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
    ) -> None:
        """When `verbose`, log what is about to be sent (never the key)."""
        if not self.verbose:
            return
        logger.info(
            "LLM request: model=%s temperature=%s max_tokens=%s; private prompts omitted",
            self.config.model,
            temperature,
            max_tokens,
        )

    def _log_verbose_response(self, llm_response: LLMResponse) -> None:
        """When `verbose`, log what came back."""
        if not self.verbose:
            return
        logger.info(
            "LLM response: tokens=%s finish_reason=%s; private content omitted",
            llm_response.tokens_used,
            llm_response.finish_reason,
        )


def _describe_openai_http_error(http_status_error: httpx.HTTPStatusError) -> ValueError:
    """Turn an OpenAI 4xx/5xx answer into a `ValueError` a person can act on."""
    failed_response = http_status_error.response
    if failed_response.status_code == 401:
        return ValueError(
            "OpenAI API authentication failed. Please check your API key. "
            "Get your key at: https://platform.openai.com/api-keys"
        )
    if failed_response.status_code == 429:
        error_detail = ""
        try:
            error_json = failed_response.json()
            error_detail = error_json.get("error", {}).get("message", "")
        except Exception:
            error_detail = ""

        return ValueError(
            f"OpenAI API rate limit or quota exceeded. {error_detail}\n"
            "This could mean:\n"
            "  - You've exceeded your rate limit (wait a moment and try again)\n"
            "  - Your account has no remaining credits\n"
            "  - Your free tier quota is exhausted\n"
            "Check your usage at: https://platform.openai.com/usage"
        )
    if failed_response.status_code == 500:
        return ValueError(
            "OpenAI API server error. The service may be temporarily unavailable. "
            "Please try again in a few moments."
        )
    # Other HTTP errors keep their status and body for context.
    return ValueError(f"OpenAI API error ({failed_response.status_code}): {failed_response.text}")


def create_client(
    provider: LLMProvider = LLMProvider.OPENAI,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1000,
) -> LLMClient:
    """
    Convenience function to create an LLM client.

    Args:
        provider: LLM provider (openai or anthropic)
        model: Model name (uses sensible defaults if not provided)
        api_key: API key (will try environment variables if not provided)
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate

    Returns:
        Configured LLMClient instance
    """
    # Set default models if not specified
    if model is None:
        if provider == LLMProvider.OPENAI:
            model = "gpt-4o"
        elif provider == LLMProvider.ANTHROPIC:
            model = "claude-3-5-sonnet-20241022"

    config = LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    return LLMClient(config)
