"""Tests for cantrips/sync_agent_files.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cantrips import sync_agent_files  # noqa: E402


def test_extract_hard_rules_returns_section_body() -> None:
    agents_md = (
        "# AGENTS.md\n\n"
        "## 3. Hard Rules (non-negotiable)\n\n"
        "- **3.1 Rule one**\n"
        "- **3.2 Rule two**\n\n"
        "## 4. Key Commands\n\n"
        "make verify\n"
    )
    extracted = sync_agent_files.extract_hard_rules(agents_md)
    assert extracted == "- **3.1 Rule one**\n- **3.2 Rule two**\n"


def test_extract_hard_rules_raises_when_heading_missing() -> None:
    with pytest.raises(ValueError, match="Heading not found"):
        sync_agent_files.extract_hard_rules("# AGENTS.md\n")


def test_evaluate_target_detects_drift_and_applies_update(tmp_path: Path) -> None:
    target_file = tmp_path / "CLAUDE.md"
    start = "<!-- sync-agent:hard-rules start -->"
    end = "<!-- sync-agent:hard-rules end -->"
    target_file.write_text(
        f"# CLAUDE\n{start}\n- old rule\n{end}\n",
        encoding="utf-8",
    )
    target = sync_agent_files.SyncTarget(
        label="claude-hard-rules",
        path=target_file,
        start_marker=start,
        end_marker=end,
    )

    drifted = sync_agent_files.evaluate_target(
        target, "- canonical rule\n", apply_changes=False
    )
    assert drifted.is_accidental_drift
    assert "- old rule" in drifted.diff
    assert "+- canonical rule" in drifted.diff

    updated = sync_agent_files.evaluate_target(
        target, "- canonical rule\n", apply_changes=True
    )
    assert updated.status == "updated"
    assert updated.updated is True

    in_sync = sync_agent_files.evaluate_target(
        target, "- canonical rule\n", apply_changes=False
    )
    assert in_sync.status == "in_sync"
    assert in_sync.is_ok


def test_evaluate_target_allows_intentional_drift(tmp_path: Path) -> None:
    target_file = tmp_path / "CLAUDE.md"
    start = "<!-- sync-agent:hard-rules start -->"
    end = "<!-- sync-agent:hard-rules end -->"
    target_file.write_text(
        f"{start}\n<!-- sync-agent:intentional -->\n- custom rule\n{end}\n",
        encoding="utf-8",
    )
    target = sync_agent_files.SyncTarget(
        label="claude-hard-rules",
        path=target_file,
        start_marker=start,
        end_marker=end,
    )

    result = sync_agent_files.evaluate_target(
        target, "- canonical rule\n", apply_changes=False
    )
    assert result.is_intentional_drift
    assert result.is_ok


def test_evaluate_target_handles_delegation_and_missing_markers(tmp_path: Path) -> None:
    delegating_file = tmp_path / "CLAUDE.md"
    delegating_file.write_text(
        "Read `AGENTS.md` first for all hard rules.\n",
        encoding="utf-8",
    )
    target = sync_agent_files.SyncTarget(
        label="claude-hard-rules",
        path=delegating_file,
        start_marker="<!-- sync-agent:hard-rules start -->",
        end_marker="<!-- sync-agent:hard-rules end -->",
    )
    delegated = sync_agent_files.evaluate_target(
        target, "- canonical rule\n", apply_changes=False
    )
    assert delegated.status == "in_sync"

    unmarked_file = tmp_path / "OTHER.md"
    unmarked_file.write_text("No markers and no delegation.\n", encoding="utf-8")
    broken_target = sync_agent_files.SyncTarget(
        label="other-rules",
        path=unmarked_file,
        start_marker="<!-- sync-agent:hard-rules start -->",
        end_marker="<!-- sync-agent:hard-rules end -->",
    )
    missing = sync_agent_files.evaluate_target(
        broken_target, "- canonical rule\n", apply_changes=False
    )
    assert missing.status == "missing_marker"
