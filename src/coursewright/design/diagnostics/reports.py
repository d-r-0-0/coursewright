"""Write Phase 1 findings to the private workspace.

Reports hold course data, so they never go into this repository: they land
in ``<workspace>/reports/diagnostics/`` (``COURSEWRIGHT_WORKSPACE``).
Writing a local report is not a Canvas write; nothing here talks to Canvas.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from coursewright.core.config.manager import resolve_workspace_directory
from coursewright.design.diagnostics.models import (
    ContextAuditReport,
    CourseDiagnostics,
    WorkspaceNotConfiguredError,
)

REPORT_FOLDER_PARTS = ("reports", "diagnostics")


def default_report_directory(workspace_directory: Path | None = None) -> Path:
    """Return the report folder inside the private workspace.

    Raises:
        WorkspaceNotConfiguredError: when no workspace is configured.
    """
    resolved_workspace = workspace_directory or resolve_workspace_directory()
    if resolved_workspace is None:
        raise WorkspaceNotConfiguredError(
            "No private workspace is configured; set COURSEWRIGHT_WORKSPACE so "
            "reports are written outside this repository."
        )
    return Path(resolved_workspace).joinpath(*REPORT_FOLDER_PARTS)


def write_report(
    report: BaseModel,
    report_name: str,
    workspace_directory: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Save *report* as timestamped JSON and return the file path.

    The file is opened exclusively so an earlier report is never overwritten.
    """
    moment = now or datetime.now()  # noqa: DTZ005 - local wall-clock stamp for a file name
    report_directory = default_report_directory(workspace_directory)
    report_directory.mkdir(parents=True, exist_ok=True)
    report_path = report_directory / f"{report_name}_{moment:%Y%m%d_%H%M%S}.json"
    with report_path.open("x", encoding="utf-8") as report_file:
        json.dump(report.model_dump(mode="json"), report_file, indent=2)
    return report_path


def save_diagnostics_report(
    diagnostics: CourseDiagnostics,
    workspace_directory: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Save a ``CourseDiagnostics`` as ``diagnostics_<course id>_<stamp>.json``."""
    return write_report(
        diagnostics, f"diagnostics_{diagnostics.course_id}", workspace_directory, now
    )


def save_context_audit_report(
    report: ContextAuditReport,
    workspace_directory: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Save a ``ContextAuditReport`` as ``text_contexts_<course id>_<stamp>.json``."""
    return write_report(report, f"text_contexts_{report.course_id}", workspace_directory, now)
