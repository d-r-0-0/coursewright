"""Tests for cantrips/lint_doc_budget.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cantrips import lint_doc_budget  # noqa: E402


def test_count_content_lines_ignores_trailing_blank_lines(tmp_path: Path) -> None:
    doc = tmp_path / "AGENTS.md"
    doc.write_text("line 1\nline 2\n\n   \n", encoding="utf-8")
    assert lint_doc_budget.count_content_lines(doc) == 2


def test_check_budget_reports_ok_warn_fail_and_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lint_doc_budget, "ROOT", tmp_path)

    status, message = lint_doc_budget.check_budget("missing.md", 10)
    assert status == "fail"
    assert "missing" in message

    doc = tmp_path / "doc.md"
    doc.write_text("\n".join(f"line {i}" for i in range(5)), encoding="utf-8")
    status, _ = lint_doc_budget.check_budget("doc.md", 10)
    assert status == "ok"

    doc.write_text("\n".join(f"line {i}" for i in range(9)), encoding="utf-8")
    status, _ = lint_doc_budget.check_budget("doc.md", 10)
    assert status == "warn"

    doc.write_text("\n".join(f"line {i}" for i in range(12)), encoding="utf-8")
    status, message = lint_doc_budget.check_budget("doc.md", 10)
    assert status == "fail"
    assert "2 over" in message


def test_repo_instruction_files_pass_budget() -> None:
    for relative_path, budget in lint_doc_budget.DOCUMENT_BUDGETS.items():
        status, message = lint_doc_budget.check_budget(relative_path, budget)
        assert status in {"ok", "warn"}, message
