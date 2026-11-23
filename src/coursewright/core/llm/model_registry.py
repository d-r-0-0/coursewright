"""LLM model registry.

Metadata helpers for displaying and estimating the cost of supported
models.  Ported from discussorator ``llm/model_registry.py``; the
prices live in ``model_pricing.json`` beside this module and are read
through ``importlib.resources`` so they load from an installed
package as well as from a checkout.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from importlib.resources import files

from coursewright.core.llm.client import LLMProvider

# Pricing older than this many days is reported as stale.
PRICING_STALE_AFTER_DAYS = 30


@dataclass(frozen=True)
class ModelMetadata:
    """Metadata describing an LLM model that Coursewright understands."""

    name: str
    provider: LLMProvider
    description: str
    input_cost_per_1k: float
    output_cost_per_1k: float
    max_output_tokens: int
    speed: str


def _load_model_pricing_payload() -> dict[str, object]:
    pricing_json_text = (
        files("coursewright.core.llm").joinpath("model_pricing.json").read_text(encoding="utf-8")
    )
    return json.loads(pricing_json_text)


def _parse_model_registry_from_payload(
    pricing_payload: dict[str, object],
) -> dict[str, ModelMetadata]:
    models_by_name = pricing_payload["models"]
    if not isinstance(models_by_name, dict):
        raise ValueError("model_pricing.json field 'models' must be a mapping")

    parsed_registry: dict[str, ModelMetadata] = {}
    for model_name, model_payload in models_by_name.items():
        if not isinstance(model_payload, dict):
            raise ValueError(f"Model '{model_name}' must map to an object")

        parsed_registry[model_name] = ModelMetadata(
            name=str(model_payload["name"]),
            provider=LLMProvider(str(model_payload["provider"])),
            description=str(model_payload["description"]),
            input_cost_per_1k=float(model_payload["input_cost_per_1k"]),
            output_cost_per_1k=float(model_payload["output_cost_per_1k"]),
            max_output_tokens=int(model_payload["max_output_tokens"]),
            speed=str(model_payload["speed"]),
        )
    return parsed_registry


MODEL_PRICING_PAYLOAD = _load_model_pricing_payload()
MODEL_REGISTRY: dict[str, ModelMetadata] = _parse_model_registry_from_payload(
    MODEL_PRICING_PAYLOAD
)


SPEED_SECONDS = {
    "very fast": 2.0,
    "fast": 4.0,
    "standard": 6.5,
    "slow": 10.0,
}


def get_pricing_updated_at() -> date:
    """Return the model pricing dataset last-updated date."""
    return date.fromisoformat(str(MODEL_PRICING_PAYLOAD["updated_at"]))


def get_pricing_age_in_days(today: date | None = None) -> int:
    """Return how many days old the pricing dataset is.

    `today` defaults to ``date.today()``; tests pass an explicit date.
    """
    return ((today or date.today()) - get_pricing_updated_at()).days


def get_pricing_staleness_warning(today: date | None = None) -> str | None:
    """Return a warning when the pricing data is more than 30 days old.

    This is the rule behind the banner on discussorator's model picker
    screen, moved here so the command line, the Textual app and a
    skill all word it the same way (AGENTS.md 3.7).  Returns ``None``
    while the data is fresh.
    """
    pricing_data_age_in_days = get_pricing_age_in_days(today)
    if pricing_data_age_in_days > PRICING_STALE_AFTER_DAYS:
        return f"Model pricing data is {pricing_data_age_in_days} days old."
    return None


def get_models(provider: LLMProvider | None = None) -> list[ModelMetadata]:
    """Return metadata for all models or for a specific provider."""
    models: Iterable[ModelMetadata] = MODEL_REGISTRY.values()
    if provider:
        models = [model for model in models if model.provider == provider]
    return sorted(models, key=lambda m: (m.provider.value, m.name))


def get_model(model_name: str) -> ModelMetadata | None:
    """Return metadata for a specific model name."""
    return MODEL_REGISTRY.get(model_name)


def estimate_feedback_cost(
    model_name: str,
    prompt_tokens: int = 450,
    completion_tokens: int | None = None,
) -> dict[str, float] | None:
    """
    Estimate the cost of generating a single student's feedback with the given model.

    Args:
        model_name: Name of the model used.
        prompt_tokens: Estimated prompt tokens (context + rubric + student data).
        completion_tokens: Estimated completion tokens (defaults to 15% of max tokens).

    Returns:
        Dict with cost breakdown or None if model metadata missing.
    """
    metadata = get_model(model_name)
    if not metadata:
        return None

    completion = (
        completion_tokens
        if completion_tokens is not None
        else int(metadata.max_output_tokens * 0.15)
    )

    prompt_cost = (prompt_tokens / 1000.0) * metadata.input_cost_per_1k
    completion_cost = (completion / 1000.0) * metadata.output_cost_per_1k

    return {
        "prompt_tokens": float(prompt_tokens),
        "completion_tokens": float(completion),
        "prompt_cost": prompt_cost,
        "completion_cost": completion_cost,
        "total_cost": prompt_cost + completion_cost,
    }


def estimate_generation_seconds(model_name: str, students: int = 1) -> float | None:
    """Estimate the wall-clock time required for generating feedback."""
    metadata = get_model(model_name)
    if not metadata:
        return None

    seconds_per_student = SPEED_SECONDS.get(metadata.speed, 8.0)
    return max(students, 1) * seconds_per_student
