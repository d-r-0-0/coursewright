"""Downloads-first discovery of files the instructor exported.

Ported and generalised from discussorator ``zybooks/importer.py``
(``default_download_directory`` and ``detect_csv``).  The original only
knew about zyBooks ``_report_`` CSV files; here the caller passes the
filename pattern of whatever export it is looking for, and every
importer shares this one search (``docs/HOUSE_STYLE.md`` section 2).

The search order is fixed:

1. the user's Downloads folder (on Windows, the registered Known
   Folder, so a Downloads folder moved into OneDrive still works);
2. the import folder inside the instructor's private workspace.

Within a folder the newest file whose *name matches the pattern* wins.
A file that does not match is never returned, however new it is:
Downloads is a shared folder full of unrelated files.

This module is library code (AGENTS.md 3.8): it returns a candidate
and never prints or prompts.  ``ui/`` shows the candidate and asks the
instructor to confirm it before anything is imported.
"""

from __future__ import annotations

import fnmatch
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from coursewright.core.config.manager import resolve_workspace_directory

# Windows "Known Folder" GUID for Downloads; the registry value follows the user
# if they moved Downloads (for example into OneDrive or onto another drive).
WINDOWS_DOWNLOADS_FOLDER_GUID = "{374DE290-123F-4565-9164-34C4FB1DD8C4}"
WINDOWS_SHELL_FOLDERS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"

# Name of the folder, inside the private workspace, where an instructor
# can drop exported files by hand instead of leaving them in Downloads.
WORKSPACE_IMPORT_FOLDER_NAME = "import"

# The zyBooks activity report, as one ready-made pattern (card
# acceptance 4): ``find_exported_file(ZYBOOKS_REPORT_FILENAME_PATTERN)``.
ZYBOOKS_REPORT_FILENAME_PATTERN = "*_report_*.csv"


class CandidateLocation(StrEnum):
    """Which of the searched folders a candidate was found in."""

    DOWNLOADS = "downloads"
    WORKSPACE_IMPORT = "workspace-import"


@dataclass(frozen=True)
class InboxCandidate:
    """A discovered file for ``ui/`` to show and the instructor to confirm.

    ``modified_at`` is timezone-aware (UTC) so the interface can render
    it in local time without guessing.
    """

    path: Path
    found_in: CandidateLocation
    modified_at: datetime


def default_download_directory() -> Path:
    """Return the user's Downloads folder, where browser exports land by default."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_SHELL_FOLDERS_KEY) as key:
                registered, _ = winreg.QueryValueEx(key, WINDOWS_DOWNLOADS_FOLDER_GUID)
            return Path(os.path.expandvars(registered))
        except OSError:
            pass
    return Path.home() / "Downloads"


def default_workspace_import_directory() -> Path | None:
    """Return the workspace import folder, or None when no workspace is configured."""
    workspace_directory = resolve_workspace_directory()
    if workspace_directory is None:
        return None
    return workspace_directory / WORKSPACE_IMPORT_FOLDER_NAME


def newest_matching_file(directory: Path, filename_pattern: str) -> tuple[Path, int] | None:
    """Return the newest file in ``directory`` whose name matches, with its mtime.

    ``filename_pattern`` is a shell-style pattern matched against the
    file name only (``fnmatch``), so it is case-insensitive on Windows
    and case-sensitive elsewhere, exactly like the ``Path.glob`` call it
    replaces.  The modified time is in nanoseconds since the epoch.

    Returns None when nothing matches, and also when the folder is
    missing or unreadable: a folder we cannot list has no candidate.
    Ties on modified time are broken by file name so the result is
    deterministic.
    """
    try:
        matches = [
            (path.stat().st_mtime_ns, path.name, path)
            for path in directory.iterdir()
            if fnmatch.fnmatch(path.name, filename_pattern) and path.is_file()
        ]
    except OSError:
        return None
    if not matches:
        return None
    modified_time_ns, _name, newest_path = max(matches, key=lambda match: match[:2])
    return newest_path, modified_time_ns


def find_exported_file(
    filename_pattern: str,
    *,
    download_directory: Path | None = None,
    workspace_import_directory: Path | None = None,
) -> InboxCandidate | None:
    """Find the newest export matching ``filename_pattern``, Downloads first.

    Downloads is searched first; the workspace import folder is only a
    fallback, used when Downloads holds no matching file.  Both folders
    default to the real ones and can be passed explicitly (tests, or an
    interface that lets the instructor point somewhere else).

    Returns None when neither folder yields a match.  The caller must
    show the candidate and get confirmation before importing it.
    """
    if download_directory is None:
        download_directory = default_download_directory()
    if workspace_import_directory is None:
        workspace_import_directory = default_workspace_import_directory()

    searched_folders = (
        (CandidateLocation.DOWNLOADS, download_directory),
        (CandidateLocation.WORKSPACE_IMPORT, workspace_import_directory),
    )
    for location, directory in searched_folders:
        if directory is None:
            continue
        found = newest_matching_file(directory, filename_pattern)
        if found is None:
            continue
        newest_path, modified_time_ns = found
        return InboxCandidate(
            path=newest_path,
            found_in=location,
            modified_at=datetime.fromtimestamp(modified_time_ns / 1_000_000_000, UTC),
        )
    return None
