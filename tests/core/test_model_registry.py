"""Tests for JSON-backed LLM model registry metadata.

Ported from discussorator ``tests/test_model_registry.py``.
"""

import json
from datetime import date
from importlib.resources import files

import pytest

from coursewright.core.llm.client import LLMConfig, LLMProvider
from coursewright.core.llm.model_registry import (
    MODEL_REGISTRY,
    ModelMetadata,
    estimate_feedback_cost,
    get_model,
    get_pricing_updated_at,
)


def test_model_pricing_json_loads_without_error() -> None:
    pricing_json_text = files("coursewright.core.llm").joinpath("model_pricing.json").read_text()
    pricing_payload = json.loads(pricing_json_text)

    assert pricing_payload["schema_version"] == 1
    assert "models" in pricing_payload


def test_each_model_payload_parses_to_model_metadata() -> None:
    pricing_json_text = files("coursewright.core.llm").joinpath("model_pricing.json").read_text()
    pricing_payload = json.loads(pricing_json_text)

    for model_name, model_payload in pricing_payload["models"].items():
        model_metadata = ModelMetadata(
            name=model_payload["name"],
            provider=LLMProvider(model_payload["provider"]),
            description=model_payload["description"],
            input_cost_per_1k=model_payload["input_cost_per_1k"],
            output_cost_per_1k=model_payload["output_cost_per_1k"],
            max_output_tokens=model_payload["max_output_tokens"],
            speed=model_payload["speed"],
        )
        assert model_metadata.name == model_name


def test_get_pricing_updated_at_returns_date() -> None:
    assert isinstance(get_pricing_updated_at(), date)


def test_known_model_prices_use_per_thousand_token_units() -> None:
    gpt_54 = get_model("gpt-5.4")
    claude_sonnet_46 = get_model("claude-sonnet-4-6")
    gpt_6_luna = get_model("gpt-6-luna")

    assert gpt_54 is not None
    assert claude_sonnet_46 is not None
    assert gpt_6_luna is not None
    assert gpt_54.input_cost_per_1k == 0.0025
    assert gpt_54.output_cost_per_1k == 0.015
    assert claude_sonnet_46.input_cost_per_1k == 0.003
    assert claude_sonnet_46.output_cost_per_1k == 0.015
    assert gpt_6_luna.input_cost_per_1k == 0.0001
    assert gpt_6_luna.output_cost_per_1k == 0.0005
    assert len(MODEL_REGISTRY) >= 2


def test_feedback_cost_uses_per_thousand_token_prices() -> None:
    estimated_cost = estimate_feedback_cost(
        "gpt-6-luna",
        prompt_tokens=1000,
        completion_tokens=1000,
    )

    assert estimated_cost is not None
    assert estimated_cost["total_cost"] == pytest.approx(0.0006)


def test_latest_openai_models_are_selectable() -> None:
    expected_model_names = {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-6-astra",
        "gpt-6.1-sol",
        "gpt-6-sol",
        "gpt-6-luna",
    }

    assert expected_model_names <= MODEL_REGISTRY.keys()
    assert expected_model_names <= set(LLMConfig().openai_models)
