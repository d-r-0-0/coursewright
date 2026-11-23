"""Application state for the Textual TUI.

``AppState`` is a plain dataclass shared by every screen: the Canvas
and LLM clients, the course list, and the active filters.  It holds
data only; screens read and write it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coursewright.core.canvas.safe_write import SafeWriteError, default_backup_directory
from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.core.config.semester import detect_current_semester


@dataclass
class AppState:
    """Shared mutable state for one Coursewright session."""

    config: AppConfig = field(default_factory=AppConfig)
    config_file: str = "config.yaml"

    def save_config(self) -> None:
        """Save preferences back to the selected configuration file."""
        self.config.save_to_file(self.config_file)

    @property
    def workspace_directory(self) -> Path | None:
        """Resolve backups and instructor content from this session's config."""
        return resolve_workspace_directory(self.config)

    @property
    def backup_directory(self) -> Path:
        """Require a private workspace before preparing a Canvas write."""
        workspace = self.workspace_directory
        if workspace is None:
            raise SafeWriteError(
                "No private workspace is configured for this session; set "
                "COURSEWRIGHT_WORKSPACE or `workspace` in the selected config."
            )
        return default_backup_directory(workspace)

    # Canvas / LLM connections (set during initialisation)
    canvas_client: Any = None
    llm_client: Any = None

    # Course data
    courses: list = field(default_factory=list)
    filtered_courses: list = field(default_factory=list)
    current_course: Any = None

    # Discussion data
    discussions: list = field(default_factory=list)
    grading_profile: str = "auto"

    # Pagination (kept for helpers that reference it)
    current_page: int = 0
    items_per_page: int = 15

    # Filters
    filter_term: str = ""
    filter_status: str = "active"
    filter_course_number: str = ""
    show_current_semester_only: bool = True
    current_semester: str = field(default_factory=detect_current_semester)
