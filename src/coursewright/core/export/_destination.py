"""Where exports go when the caller does not say.

discussorator wrote to a relative ``exports/`` folder, which lands
inside whatever directory the process happened to start in -- for a
developer that is usually the repository.  Coursewright's public repo
must never hold instructor output (AGENTS.md 3.13), so the default is
the instructor's private workspace instead.

Also home to the one filesystem-name sanitizer the three exporters
share, so the rule lives in a single place.
"""

from __future__ import annotations

import re
from pathlib import Path

from coursewright.core.config.manager import resolve_workspace_directory

EXPORT_SUBDIRECTORY_NAME = "exports"


class ExportDestinationError(ValueError):
    """Raised when no output location was given and none is configured."""


def default_export_base_directory() -> Path:
    """Return ``<private workspace>/exports``.

    The workspace comes from ``COURSEWRIGHT_WORKSPACE`` or the config
    file.  When neither is set we refuse to guess, because any guess
    (the current directory, the repo) risks publishing private files.
    """
    workspace_directory = resolve_workspace_directory()
    if workspace_directory is None:
        raise ExportDestinationError(
            "No output location given and no private workspace configured. "
            "Set COURSEWRIGHT_WORKSPACE or pass an explicit output path."
        )
    return workspace_directory / EXPORT_SUBDIRECTORY_NAME


def sanitize_name_for_filesystem(name: str, max_length: int = 50) -> str:
    """Return a filesystem-safe version of ``name``."""
    # Remove characters that aren't filesystem-safe
    sanitized = re.sub(r'[<>:"/\\|?*]', "", name)
    # Collapse runs of whitespace into one space
    sanitized = re.sub(r"\s+", " ", sanitized)
    sanitized = sanitized.strip()
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].strip()
    # Underscores make cleaner paths than spaces
    return sanitized.replace(" ", "_")
