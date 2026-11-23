"""Tests for the LLM cost tracker.

Ported from discussorator ``tests/test_cost_tracker.py``: usage
recording, persistence, monthly queries and budget alerts.  Every
test writes under ``tmp_path``, never to the real usage file.
"""

from __future__ import annotations

import json
from datetime import datetime

from coursewright.core.llm.cost_tracker import (
    UsageLog,
    UsageRecord,
    check_budget_alert,
    record_usage,
)

# ---------------------------------------------------------------------------
# UsageRecord basics
# ---------------------------------------------------------------------------


class TestUsageRecord:
    def test_fields(self):
        r = UsageRecord(
            timestamp="2026-02-10T12:00:00",
            model="gpt-5-nano",
            provider="openai",
            tokens_used=500,
            estimated_cost_usd=0.003,
        )
        assert r.tokens_used == 500
        assert r.estimated_cost_usd == 0.003


# ---------------------------------------------------------------------------
# UsageLog persistence
# ---------------------------------------------------------------------------


class TestUsageLogPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "usage.json"
        record = UsageRecord(
            timestamp="2026-02-10T12:00:00",
            model="gpt-5-nano",
            provider="openai",
            tokens_used=100,
            estimated_cost_usd=0.001,
        )
        log = UsageLog(records=[record])
        log.save(path)
        loaded = UsageLog.load(path)
        assert len(loaded.records) == 1
        assert loaded.records[0].model == "gpt-5-nano"

    def test_load_missing_file_returns_empty(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        log = UsageLog.load(path)
        assert log.records == []

    def test_load_corrupt_json_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json!!")
        log = UsageLog.load(path)
        assert log.records == []


# ---------------------------------------------------------------------------
# UsageLog queries
# ---------------------------------------------------------------------------


class TestUsageLogQueries:
    def _make_log(self) -> UsageLog:
        return UsageLog(
            records=[
                UsageRecord(
                    timestamp="2026-02-05T10:00:00",
                    model="gpt-5-nano",
                    provider="openai",
                    tokens_used=200,
                    estimated_cost_usd=0.01,
                ),
                UsageRecord(
                    timestamp="2026-02-10T10:00:00",
                    model="gpt-5-nano",
                    provider="openai",
                    tokens_used=300,
                    estimated_cost_usd=0.02,
                ),
                UsageRecord(
                    timestamp="2026-01-15T10:00:00",
                    model="claude-3-5-haiku-20241022",
                    provider="anthropic",
                    tokens_used=400,
                    estimated_cost_usd=0.05,
                ),
            ]
        )

    def test_monthly_spend(self):
        log = self._make_log()
        assert log.monthly_spend(2026, 2) == 0.03

    def test_monthly_records(self):
        log = self._make_log()
        assert len(log.monthly_records(2026, 2)) == 2

    def test_total_spend(self):
        log = self._make_log()
        assert log.total_spend() == 0.08

    def test_total_tokens(self):
        log = self._make_log()
        assert log.total_tokens() == 900


# ---------------------------------------------------------------------------
# record_usage (end-to-end)
# ---------------------------------------------------------------------------


class TestRecordUsage:
    def test_appends_to_file(self, tmp_path):
        path = tmp_path / "usage.json"
        record_usage("gpt-5-nano", "openai", 100, usage_file=path)
        record_usage("gpt-5-nano", "openai", 200, usage_file=path)

        data = json.loads(path.read_text())
        assert len(data) == 2

    def test_returns_record(self, tmp_path):
        path = tmp_path / "usage.json"
        r = record_usage("gpt-5-nano", "openai", 100, usage_file=path)
        assert r.tokens_used == 100
        assert r.estimated_cost_usd > 0


# ---------------------------------------------------------------------------
# check_budget_alert
# ---------------------------------------------------------------------------


class TestCheckBudgetAlert:
    def test_no_alert_when_under_budget(self, tmp_path):
        path = tmp_path / "usage.json"
        record_usage("gpt-5-nano", "openai", 10, usage_file=path)
        alert = check_budget_alert(100.0, usage_file=path)
        assert alert is None

    def test_alert_when_over_budget(self, tmp_path):
        path = tmp_path / "usage.json"
        # Pin the reference date so the test never drifts across a
        # month boundary — the record and the alert query both use
        # the same year-month.
        reference = datetime(2026, 6, 15, 12, 0, 0)
        log = UsageLog(
            records=[
                UsageRecord(
                    timestamp=reference.strftime("%Y-%m-%dT%H:%M:%S"),
                    model="test",
                    provider="openai",
                    tokens_used=0,
                    estimated_cost_usd=150.0,
                )
            ]
        )
        log.save(path)
        alert = check_budget_alert(100.0, usage_file=path, reference_date=reference)
        assert alert is not None
        assert "exceeded" in alert.lower()

    def test_no_budget_no_alert(self, tmp_path):
        path = tmp_path / "usage.json"
        alert = check_budget_alert(0.0, usage_file=path)
        assert alert is None
