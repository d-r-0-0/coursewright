"""Helper utilities for initializing LLM clients from CLI commands."""

from __future__ import annotations

from coursewright.core.config.settings import AppConfig
from coursewright.core.llm.client import LLMClient, LLMProvider
from coursewright.core.llm.client import LLMConfig as LLMClientConfig
from coursewright.core.llm.model_registry import estimate_feedback_cost
from coursewright.ta.grading.feedback_generator import FeedbackGenerator


class LLMInitializationError(RuntimeError):
    """Raised when we cannot create an LLM client."""


def normalize_provider(provider_name: str | None, config: AppConfig) -> tuple[str, LLMProvider]:
    """Resolve a provider string into the client enum."""
    chosen = (
        provider_name or config.llm.provider or config.llm.default_provider or "openai"
    ).lower()
    if chosen not in ("openai", "anthropic"):
        raise LLMInitializationError(
            f"Unsupported provider '{provider_name}'. Choose openai or anthropic."
        )

    provider_enum = LLMProvider.OPENAI if chosen == "openai" else LLMProvider.ANTHROPIC
    return chosen, provider_enum


def default_model_for_provider(provider_name: str) -> str:
    """Return a sensible default model for the provider."""
    return "gpt-4o-mini" if provider_name == "openai" else "claude-3-5-haiku-20241022"


def resolve_model_name(config: AppConfig, provider_name: str, model_override: str | None) -> str:
    """Determine which model to use when none is explicitly supplied."""
    if model_override:
        return model_override

    if config.llm.last_used_model and config.llm.last_used_provider == provider_name:
        return config.llm.last_used_model

    if config.llm.default_model:
        return config.llm.default_model

    return default_model_for_provider(provider_name)


def create_feedback_generator(
    config: AppConfig,
    provider_override: str | None = None,
    model_override: str | None = None,
    verbose: bool = False,
) -> tuple[FeedbackGenerator, str, str]:
    """Build a FeedbackGenerator based on configuration and overrides."""
    provider_name, provider_enum = normalize_provider(provider_override, config)
    model_name = resolve_model_name(config, provider_name, model_override)

    llm_config = LLMClientConfig(
        provider=provider_enum,
        api_key=config.llm.get_api_key(),
        model=model_name,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
    )

    client = LLMClient(llm_config, verbose=verbose)
    generator = FeedbackGenerator(client, verbose=verbose)
    return generator, provider_name, model_name


def get_cost_summary(
    model_name: str,
    max_tokens: int,
    student_count: int = 1,
    prompt_tokens: int = 450,
) -> dict | None:
    """Return an estimated cost summary for N students."""
    cost = estimate_feedback_cost(
        model_name, prompt_tokens=prompt_tokens, completion_tokens=max_tokens
    )
    if not cost:
        return None

    total_cost = cost["total_cost"] * max(student_count, 1)
    return {
        "per_student": cost["total_cost"],
        "total": total_cost,
        "prompt_tokens": cost["prompt_tokens"],
        "completion_tokens": cost["completion_tokens"],
    }
