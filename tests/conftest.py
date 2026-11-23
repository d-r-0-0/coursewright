"""Suite-wide test isolation."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Run every test from its own empty directory.

    ``AppConfig.load_from_file()`` creates a default ``config.yaml`` in the
    current directory when none exists, and the CLI and TUI call it at
    startup.  Without this, any test that boots the app writes that file into
    the repository root, where the secrets gate stage then flags it.
    """
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))

