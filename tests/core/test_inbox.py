"""Tests for Downloads-first discovery (ported from discussorator's detection tests)."""

from __future__ import annotations

import ast
import os
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coursewright.core import inbox
from coursewright.core.inbox import discovery

REPORT_PATTERN = inbox.ZYBOOKS_REPORT_FILENAME_PATTERN


def write_file_modified_at(path: Path, modified_epoch_seconds: int) -> Path:
    """Create a small file with a fixed modified time, so 'newest' is not a race."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a,b\n", encoding="utf-8")
    os.utime(path, (modified_epoch_seconds, modified_epoch_seconds))
    return path


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """A Downloads folder and a workspace import folder, both empty."""
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    downloads = tmp_path / "Downloads"
    workspace_import = tmp_path / "workspace" / inbox.WORKSPACE_IMPORT_FOLDER_NAME
    downloads.mkdir()
    workspace_import.mkdir(parents=True)
    return downloads, workspace_import


def search(folders, pattern: str = REPORT_PATTERN) -> inbox.InboxCandidate | None:
    downloads, workspace_import = folders
    return inbox.find_exported_file(
        pattern,
        download_directory=downloads,
        workspace_import_directory=workspace_import,
    )


# --- ported from discussorator tests/test_zybooks_importer.py -----------------


def test_shared_folder_ignores_unrelated_files(folders):
    """Downloads holds bank exports etc.; only files matching the pattern qualify."""
    downloads, _workspace_import = folders
    write_file_modified_at(downloads / "bank_statement.csv", 2_000)
    assert search(folders) is None
    report = write_file_modified_at(downloads / "EXAMPLE101_report_1_2026-10-24.csv", 1_000)
    candidate = search(folders)
    assert candidate is not None
    assert candidate.path == report


def test_missing_folders_return_none(tmp_path):
    assert (
        inbox.find_exported_file(
            REPORT_PATTERN,
            download_directory=tmp_path / "missing",
            workspace_import_directory=tmp_path / "also-missing",
        )
        is None
    )


def test_default_download_directory_falls_back_to_home_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery.sys, "platform", "linux")
    monkeypatch.setattr(discovery.Path, "home", lambda: tmp_path)
    assert inbox.default_download_directory() == tmp_path / "Downloads"


# --- card acceptance 3 ---------------------------------------------------------


def test_match_in_downloads_beats_older_match_in_workspace_folder(folders):
    downloads, workspace_import = folders
    write_file_modified_at(workspace_import / "EXAMPLE101_report_1.csv", 1_000)
    newer = write_file_modified_at(downloads / "EXAMPLE101_report_2.csv", 2_000)
    candidate = search(folders)
    assert candidate == inbox.InboxCandidate(
        path=newer,
        found_in=inbox.CandidateLocation.DOWNLOADS,
        modified_at=datetime.fromtimestamp(2_000, UTC),
    )


def test_no_match_returns_none(folders):
    downloads, workspace_import = folders
    write_file_modified_at(downloads / "notes.txt", 1_000)
    write_file_modified_at(workspace_import / "gradebook.csv", 1_000)
    assert search(folders) is None


def test_unreadable_folder_returns_none(folders, monkeypatch):
    downloads, workspace_import = folders
    write_file_modified_at(downloads / "EXAMPLE101_report_1.csv", 1_000)
    write_file_modified_at(workspace_import / "EXAMPLE101_report_1.csv", 1_000)

    def refuse_to_list(_self):
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "iterdir", refuse_to_list)
    assert inbox.newest_matching_file(downloads, REPORT_PATTERN) is None
    assert search(folders) is None


def test_non_matching_newest_file_is_ignored(folders):
    downloads, _workspace_import = folders
    report = write_file_modified_at(downloads / "EXAMPLE101_report_1.csv", 1_000)
    write_file_modified_at(downloads / "bank_statement.csv", 9_000)
    candidate = search(folders)
    assert candidate is not None
    assert candidate.path == report


# --- the generalised search ------------------------------------------------------


def test_newest_match_wins_within_a_folder(folders):
    downloads, _workspace_import = folders
    write_file_modified_at(downloads / "EXAMPLE101_report_1.csv", 1_000)
    newest = write_file_modified_at(downloads / "EXAMPLE101_report_2.csv", 3_000)
    write_file_modified_at(downloads / "EXAMPLE101_report_3.csv", 2_000)
    assert search(folders).path == newest


def test_equal_modified_times_are_broken_by_name(folders):
    downloads, _workspace_import = folders
    write_file_modified_at(downloads / "a_report_1.csv", 1_000)
    later_name = write_file_modified_at(downloads / "b_report_1.csv", 1_000)
    assert search(folders).path == later_name


def test_workspace_folder_is_the_fallback_when_downloads_has_no_match(folders):
    downloads, workspace_import = folders
    write_file_modified_at(downloads / "bank_statement.csv", 9_000)
    report = write_file_modified_at(workspace_import / "EXAMPLE101_report_1.csv", 1_000)
    candidate = search(folders)
    assert candidate.path == report
    assert candidate.found_in is inbox.CandidateLocation.WORKSPACE_IMPORT
    assert candidate.modified_at == datetime.fromtimestamp(1_000, UTC)


def test_downloads_match_wins_even_when_workspace_match_is_newer(folders):
    """Downloads first (HOUSE_STYLE.md section 2): the workspace is only a fallback."""
    downloads, workspace_import = folders
    in_downloads = write_file_modified_at(downloads / "EXAMPLE101_report_1.csv", 1_000)
    write_file_modified_at(workspace_import / "EXAMPLE101_report_2.csv", 5_000)
    assert search(folders).path == in_downloads


def test_unreadable_downloads_still_falls_back_to_workspace(folders):
    _downloads, workspace_import = folders
    report = write_file_modified_at(workspace_import / "EXAMPLE101_report_1.csv", 1_000)
    candidate = inbox.find_exported_file(
        REPORT_PATTERN,
        download_directory=workspace_import.parent / "missing",
        workspace_import_directory=workspace_import,
    )
    assert candidate.path == report


def test_a_folder_named_like_an_export_is_not_a_candidate(folders):
    downloads, _workspace_import = folders
    (downloads / "EXAMPLE101_report_1.csv").mkdir()
    assert search(folders) is None


def test_caller_supplies_any_pattern(folders):
    downloads, _workspace_import = folders
    write_file_modified_at(downloads / "EXAMPLE101_report_1.csv", 9_000)
    export = write_file_modified_at(downloads / "course-export-42.imscc", 1_000)
    assert search(folders, "course-export-*.imscc").path == export


def test_zybooks_report_is_one_call_with_default_folders(tmp_path, monkeypatch):
    """Card acceptance 4, and the default folders: Downloads plus the workspace."""
    monkeypatch.setattr(discovery.sys, "platform", "linux")
    monkeypatch.setattr(discovery.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path / "workspace"))
    assert inbox.find_exported_file(inbox.ZYBOOKS_REPORT_FILENAME_PATTERN) is None

    workspace_report = write_file_modified_at(
        tmp_path / "workspace" / "import" / "EXAMPLE101_report_1.csv", 1_000
    )
    candidate = inbox.find_exported_file(inbox.ZYBOOKS_REPORT_FILENAME_PATTERN)
    assert candidate.path == workspace_report
    assert candidate.found_in is inbox.CandidateLocation.WORKSPACE_IMPORT

    downloads_report = write_file_modified_at(
        tmp_path / "Downloads" / "EXAMPLE101_report_2.csv", 2_000
    )
    candidate = inbox.find_exported_file(inbox.ZYBOOKS_REPORT_FILENAME_PATTERN)
    assert candidate.path == downloads_report
    assert candidate.found_in is inbox.CandidateLocation.DOWNLOADS


def test_no_workspace_configured_means_no_workspace_import_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert inbox.default_workspace_import_directory() is None
    assert (
        inbox.find_exported_file(REPORT_PATTERN, download_directory=tmp_path / "Downloads") is None
    )


# --- Windows Known Folder lookup, with a fake winreg ------------------------------


class FakeRegistryKey:
    """Stands in for the context manager ``winreg.OpenKey`` returns."""

    def __enter__(self):
        return self

    def __exit__(self, *_exception_details):
        return False


def install_fake_winreg(monkeypatch, registered_value: str | None):
    """Make ``import winreg`` work here; None means the lookup fails with OSError."""
    opened_keys: list[tuple[object, str]] = []
    queried_values: list[str] = []

    def open_key(root, sub_key):
        opened_keys.append((root, sub_key))
        return FakeRegistryKey()

    def query_value_ex(_key, value_name):
        queried_values.append(value_name)
        if registered_value is None:
            raise FileNotFoundError("no such registry value")
        return registered_value, 2

    fake_winreg = types.SimpleNamespace(
        HKEY_CURRENT_USER="fake-hkey-current-user",
        OpenKey=open_key,
        QueryValueEx=query_value_ex,
    )
    monkeypatch.setitem(discovery.sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(discovery.sys, "platform", "win32")
    return opened_keys, queried_values


def test_windows_uses_the_registered_known_folder(monkeypatch, tmp_path):
    moved_downloads = tmp_path / "OneDrive" / "Downloads"
    opened_keys, queried_values = install_fake_winreg(monkeypatch, str(moved_downloads))
    monkeypatch.setattr(discovery.Path, "home", lambda: tmp_path / "home")

    assert inbox.default_download_directory() == moved_downloads
    assert opened_keys == [("fake-hkey-current-user", inbox.WINDOWS_SHELL_FOLDERS_KEY)]
    assert queried_values == [inbox.WINDOWS_DOWNLOADS_FOLDER_GUID]


def test_windows_expands_environment_variables_in_the_registered_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_USER_PROFILE", str(tmp_path))
    install_fake_winreg(monkeypatch, "${FAKE_USER_PROFILE}/Downloads")
    assert inbox.default_download_directory() == tmp_path / "Downloads"


def test_windows_falls_back_to_home_downloads_when_registry_lookup_fails(monkeypatch, tmp_path):
    install_fake_winreg(monkeypatch, None)
    monkeypatch.setattr(discovery.Path, "home", lambda: tmp_path)
    assert inbox.default_download_directory() == tmp_path / "Downloads"


# --- layering (card acceptance 2) ----------------------------------------------------


def test_inbox_package_imports_no_user_interface_library():
    forbidden_roots = {"rich", "typer", "textual"}
    violations = []
    for source_path in Path(inbox.__file__).parent.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported_names = [node.module or ""]
            else:
                continue
            for imported_name in imported_names:
                if imported_name.split(".")[0] in forbidden_roots or imported_name.startswith(
                    "coursewright.ui"
                ):
                    violations.append(f"{source_path.name}: {imported_name}")
    assert not violations, "\n".join(violations)
