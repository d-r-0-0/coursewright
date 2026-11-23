"""LLM cost tracker: persistent token usage logging.

Records token usage and estimated cost after each LLM API call.
Data is persisted to ``~/.coursewright/usage.json`` so costs survive
across sessions.  Ported from discussorator ``llm/cost_tracker.py``
(which wrote to ``~/.discussorator/usage.json``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from coursewright.core.llm.model_registry import get_model

# Default storage location
_DEFAULT_USAGE_FILE = Path.home() / ".coursewright" / "usage.json"


@dataclass
class UsageRecord:
    """A single LLM API call's usage data."""

    timestamp: str
    model: str
    provider: str
    tokens_used: int
    estimated_cost_usd: float
    description: str = ""


@dataclass
class UsageLog:
    """Collection of usage records with query helpers."""

    records: list[UsageRecord] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path = _DEFAULT_USAGE_FILE) -> None:
        """Write the log to *path* as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(r) for r in self.records]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = _DEFAULT_USAGE_FILE) -> UsageLog:
        """Load from *path*, returning an empty log if missing."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records = [UsageRecord(**item) for item in data]
            return cls(records=records)
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def monthly_spend(self, year: int, month: int) -> float:
        """Total estimated cost for a given year/month."""
        prefix = f"{year:04d}-{month:02d}"
        return sum(r.estimated_cost_usd for r in self.records if r.timestamp.startswith(prefix))

    def monthly_records(self, year: int, month: int) -> list[UsageRecord]:
        """All records for a given year/month."""
        prefix = f"{year:04d}-{month:02d}"
        return [r for r in self.records if r.timestamp.startswith(prefix)]

    def total_spend(self) -> float:
        return sum(r.estimated_cost_usd for r in self.records)

    def total_tokens(self) -> int:
        return sum(r.tokens_used for r in self.records)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def _estimate_cost(model_name: str, tokens: int) -> float:
    """Rough cost estimate using the model registry pricing."""
    metadata = get_model(model_name)
    if not metadata or tokens == 0:
        return 0.0
    # Assume roughly 60 % input / 40 % output split when we only
    # have a total token count.
    input_tokens = int(tokens * 0.6)
    output_tokens = tokens - input_tokens
    cost = (
        metadata.input_cost_per_1k * input_tokens / 1000
        + metadata.output_cost_per_1k * output_tokens / 1000
    )
    return round(cost, 6)


def record_usage(
    model: str,
    provider: str,
    tokens_used: int,
    description: str = "",
    usage_file: Path = _DEFAULT_USAGE_FILE,
) -> UsageRecord:
    """
    Append a usage record to the persistent log.

    Call this after every successful LLM API response.
    """
    cost = _estimate_cost(model, tokens_used)
    record = UsageRecord(
        timestamp=datetime.now().isoformat(),
        model=model,
        provider=provider,
        tokens_used=tokens_used,
        estimated_cost_usd=cost,
        description=description,
    )

    log = UsageLog.load(usage_file)
    log.records.append(record)
    log.save(usage_file)
    return record


def check_budget_alert(
    monthly_budget_usd: float,
    usage_file: Path = _DEFAULT_USAGE_FILE,
    reference_date: datetime | None = None,
) -> str | None:
    """
    Return a warning string if current-month spend exceeds 80 %
    of *monthly_budget_usd*, or ``None`` if under budget.

    *reference_date* controls which calendar month to check.  Defaults
    to ``datetime.now()`` when ``None`` — callers (especially tests)
    can pass an explicit date to avoid month-boundary flakiness.
    """
    if monthly_budget_usd <= 0:
        return None

    now = reference_date or datetime.now()
    log = UsageLog.load(usage_file)
    spent = log.monthly_spend(now.year, now.month)
    ratio = spent / monthly_budget_usd

    if ratio >= 1.0:
        return f"Budget exceeded: ${spent:.2f} / ${monthly_budget_usd:.2f} ({ratio:.0%})"
    if ratio >= 0.8:
        return f"Approaching budget: ${spent:.2f} / ${monthly_budget_usd:.2f} ({ratio:.0%})"
    return None
