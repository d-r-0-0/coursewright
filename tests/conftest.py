"""Suite-wide test isolation."""

from __future__ import annotations

import os

import pytest

# Typer decides once, when it is first imported, whether to force terminal
# styling on its help output, and it does so whenever GITHUB_ACTIONS,
# FORCE_COLOR or PY_COLORS is set.  Styled help splits option names with
# escape codes ("--" + codes + "execute"), so every test that reads --help
# passed locally and failed in CI.  This must run before any test module
# imports Typer, which is why it is here at import time and not in a fixture.
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"


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

