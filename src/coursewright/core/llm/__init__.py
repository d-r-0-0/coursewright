"""LLM client, model registry, prompts and cost tracking.

`client` is the one client for LLM providers; `model_registry` holds
model metadata and prices; `cost_tracker` keeps the usage log;
`prompt_library` loads the bundled prompt files.  The names most
callers need are re-exported here.
"""

from __future__ import annotations

from coursewright.core.llm.client import (
    LLMClient,
    LLMConfig,
    LLMProvider,
    LLMResponse,
    create_client,
)
from coursewright.core.llm.cost_tracker import (
    UsageLog,
    UsageRecord,
    check_budget_alert,
    record_usage,
)
from coursewright.core.llm.model_registry import (
    ModelMetadata,
    estimate_feedback_cost,
    estimate_generation_seconds,
    get_model,
    get_models,
    get_pricing_staleness_warning,
    get_pricing_updated_at,
)
from coursewright.core.llm.prompt_library import list_prompt_names, load_prompt

__all__ = [
    "LLMClient",
    "LLMConfig",
    "LLMProvider",
    "LLMResponse",
    "ModelMetadata",
    "UsageLog",
    "UsageRecord",
    "check_budget_alert",
    "create_client",
    "estimate_feedback_cost",
    "estimate_generation_seconds",
    "get_model",
    "get_models",
    "get_pricing_staleness_warning",
    "get_pricing_updated_at",
    "list_prompt_names",
    "load_prompt",
    "record_usage",
]
