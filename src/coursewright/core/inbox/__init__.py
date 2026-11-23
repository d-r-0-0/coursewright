"""Find files the instructor exported: Downloads folder first.

One call per export, for example the zyBooks activity report::

    candidate = find_exported_file(ZYBOOKS_REPORT_FILENAME_PATTERN)

The result is a candidate for ``ui/`` to show and confirm, never a
file to import unasked (``docs/HOUSE_STYLE.md`` section 2).
"""

from __future__ import annotations

from coursewright.core.inbox.discovery import (
    WINDOWS_DOWNLOADS_FOLDER_GUID,
    WINDOWS_SHELL_FOLDERS_KEY,
    WORKSPACE_IMPORT_FOLDER_NAME,
    ZYBOOKS_REPORT_FILENAME_PATTERN,
    CandidateLocation,
    InboxCandidate,
    default_download_directory,
    default_workspace_import_directory,
    find_exported_file,
    newest_matching_file,
)

__all__ = [
    "WINDOWS_DOWNLOADS_FOLDER_GUID",
    "WINDOWS_SHELL_FOLDERS_KEY",
    "WORKSPACE_IMPORT_FOLDER_NAME",
    "ZYBOOKS_REPORT_FILENAME_PATTERN",
    "CandidateLocation",
    "InboxCandidate",
    "default_download_directory",
    "default_workspace_import_directory",
    "find_exported_file",
    "newest_matching_file",
]
