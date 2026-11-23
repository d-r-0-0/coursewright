"""Exports default to the private workspace, never the working directory."""

from pathlib import Path

import pytest

from coursewright.core.canvas.models import DiscussionAnalysis
from coursewright.core.config.manager import WORKSPACE_ENVIRONMENT_VARIABLE
from coursewright.core.export import CSVExporter
from coursewright.core.export._destination import (
    ExportDestinationError,
    default_export_base_directory,
    sanitize_name_for_filesystem,
)


@pytest.fixture
def small_analysis() -> DiscussionAnalysis:
    return DiscussionAnalysis(discussion_id=1, discussion_title="Week 1: Intro?", course_id=7)


def test_default_base_directory_is_workspace_exports(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKSPACE_ENVIRONMENT_VARIABLE, str(tmp_path))
    assert default_export_base_directory() == tmp_path / "exports"


def test_default_export_lands_in_workspace_not_current_directory(
    tmp_path, monkeypatch, small_analysis
):
    workspace = tmp_path / "workspace"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(WORKSPACE_ENVIRONMENT_VARIABLE, str(workspace))
    monkeypatch.chdir(elsewhere)

    written = Path(CSVExporter().export_analysis(small_analysis))

    assert workspace / "exports" / "course_7" in written.parents
    assert not (elsewhere / "exports").exists()


def test_missing_workspace_refuses_instead_of_guessing(tmp_path, monkeypatch, small_analysis):
    monkeypatch.delenv(WORKSPACE_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ExportDestinationError):
        CSVExporter().export_analysis(small_analysis)

    assert list(tmp_path.iterdir()) == []


def test_sanitizer_strips_unsafe_characters_and_truncates():
    assert sanitize_name_for_filesystem('a<b>:"c/d\\e|f?g*  h') == "abcdefg_h"
    assert len(sanitize_name_for_filesystem("x" * 80)) == 50
